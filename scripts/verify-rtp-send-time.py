"""Exercise departure stamping through actual pacer wrappers and real SRTP."""
import asyncio
import os
import struct
from types import SimpleNamespace
from unittest.mock import patch

from pylibsrtp import Policy, Session
from aiortc.rtp import HeaderExtensionsMap, RtpPacket, pack_header_extensions, wrap_rtx
from aiortc.rtcrtpparameters import RTCRtpHeaderExtensionParameters, RTCRtpParameters
from rtp_send_time import ABS_SEND_TIME_URI, _extension_position, install_rtp_send_time
from rtp_scheduling import install_cooperative_rtp_send
from turn_flow_control import install_turn_flow_control


def parameters(identifier=3):
    return RTCRtpParameters(headerExtensions=[] if identifier is None else [
        RTCRtpHeaderExtensionParameters(id=identifier, uri=ABS_SEND_TIME_URI)])


def packet(identifier=3, *, ssrc=123, sequence=20, profile_bits=0, extensions=None):
    extensions = extensions if extensions is not None else [(identifier, b'old'), (8, b'unknown')]
    profile, raw = pack_header_extensions(extensions)
    if profile == 0x1000:
        profile |= profile_bits
    # Two CSRCs verify that the extension offset is not assumed to be 16.
    return (struct.pack('!BBHIIII', 0x92, 0x80 | 102, sequence, 90000, ssrc, 987, 654)
            + struct.pack('!HH', profile, len(raw) // 4) + raw + b'video-payload')


def sender(transport, ssrc=123, rtx=456):
    async def send(parameters):
        value._RTCRtpSender__started = True
    async def stop():
        value.stopped = True
    value = SimpleNamespace(kind='video', transport=transport, _ssrc=ssrc, _rtx_ssrc=rtx,
                            send=send, stop=stop, _RTCRtpSender__started=False,
                            _RTCRtpSender__rtp_task=asyncio.current_task(),
                            _RTCRtpSender__encoder=SimpleNamespace(fixed_profile=True, profile_bitrate=8_000_000))
    return value


async def main():
    now = [100 << 32]
    sent = []
    async def record(data):
        sent.append((now[0], data))
    pair = SimpleNamespace(local_candidate=SimpleNamespace(type='relay'),
                           remote_candidate=SimpleNamespace(type='relay'),
                           protocol=SimpleNamespace(transport=object()))
    transport = SimpleNamespace(_send_rtp=record, state='connected',
                                transport=SimpleNamespace(_connection=SimpleNamespace(
                                    _nominated={1:pair}, _closed=False, turn_transport='udp')))
    video = sender(transport)
    state = install_rtp_send_time(video, ntp_clock=lambda: now[0])
    assert install_rtp_send_time(video) is state
    await video.send(parameters())
    install_cooperative_rtp_send(transport)
    class DelayedPacer:
        next_at = 0
        async def wait(self, size, budget):
            # The deterministic 600ms wait represents a distant TCP queue.
            # Its time must not be included in the packet's departure stamp.
            now[0] += round(.6 * (1 << 32))
            await asyncio.sleep(0)
    with patch('turn_flow_control.PacketPacer', DelayedPacer):
        install_turn_flow_control(transport, [102,103], video_sender=video)
    original = packet()
    await transport._send_rtp(original)
    stamp = ((sent[-1][0] >> 14) & 0xFFFFFF).to_bytes(3,'big')
    position = _extension_position(original,3)
    assert sent[-1][1] == original[:position] + stamp + original[position+3:]
    assert original[position:position+3] == b'old'
    repaired = packet(ssrc=456, sequence=21)
    await transport._send_rtp(repaired)
    assert state['packetsStamped'] == 2 and state['rtxPacketsStamped'] == 1
    assert sent[-1][1][_extension_position(repaired,3):][:3] == ((now[0] >> 14) & 0xFFFFFF).to_bytes(3,'big')
    assert repaired[_extension_position(repaired,3):][:3] == b'old'

    # A second sender sharing DTLS may use another actually negotiated ID.
    second = sender(transport, 789, 987)
    assert install_rtp_send_time(second) is state
    await second.send(parameters(18))
    two = packet(18, ssrc=789, sequence=22, profile_bits=7)
    await transport._send_rtp(two)
    two_position = _extension_position(two,18)
    assert struct.unpack_from('!H',sent[-1][1],20)[0] == 0x1007
    assert sent[-1][1][:two_position] == two[:two_position]
    assert sent[-1][1][two_position+3:] == two[two_position+3:]
    assert state['negotiatedSenders'] == 2
    await second.send(parameters(5))
    assert 789 in transport._desktop_rtp_send_time.bindings
    assert transport._desktop_rtp_send_time.bindings[789][0] == 18

    missing = packet(extensions=[(8,b'unknown')])
    await transport._send_rtp(missing)
    assert sent[-1][1] == missing and state['missingExtensions'] == 1
    duplicate = packet(extensions=[(3,b'old'),(3,b'two')])
    await transport._send_rtp(duplicate)
    assert sent[-1][1] == duplicate and state['invalidExtensions'] == 1
    malformed = original[:20] + struct.pack('!HH',0xBEDE,65535) + b'bad'
    await transport._send_rtp(malformed)
    assert sent[-1][1] == malformed and state['invalidExtensions'] == 2
    other = packet(ssrc=111)
    await transport._send_rtp(other)
    assert sent[-1][1] == other
    rtcp = bytes([0x80,200,0,6])+bytes(24)
    await transport._send_rtp(rtcp)
    assert sent[-1][1] == rtcp
    await second.stop()
    assert state['negotiatedSenders'] == 1 and 789 not in transport._desktop_rtp_send_time.bindings
    await video.stop()
    assert state['negotiatedSenders'] == 0

    # Never modify an extension merely because an unnegotiated packet has it.
    plain = SimpleNamespace(_send_rtp=record)
    no_extension = sender(plain)
    no_state = install_rtp_send_time(no_extension)
    await no_extension.send(parameters(None))
    await plain._send_rtp(original)
    assert sent[-1][1] == original and no_state['packetsStamped'] == 0
    await no_extension.stop()

    wrong_order = SimpleNamespace(_send_rtp=record, _cooperative_rtp_send={})
    try:
        install_rtp_send_time(sender(wrong_order))
    except RuntimeError:
        pass
    else:
        raise AssertionError('Misordered installation must not silently stamp before pacing')

    # Real SRTP authenticates the restamped bytes, on primary and RTX SSRCs.
    key = os.urandom(30)
    outbound = Session(Policy(key=key,ssrc_type=Policy.SSRC_ANY_OUTBOUND))
    inbound = Session(Policy(key=key,ssrc_type=Policy.SSRC_ANY_INBOUND))
    encrypted = []
    async def encrypt(data):
        wire = outbound.protect(data)
        recovered = inbound.unprotect(wire)
        assert recovered == data
        encrypted.append(recovered)
    crypto_transport = SimpleNamespace(_send_rtp=encrypt)
    crypto_sender = sender(crypto_transport)
    crypto_state = install_rtp_send_time(crypto_sender,ntp_clock=lambda:now[0])
    await crypto_sender.send(parameters())
    for data in [original, repaired]:
        await crypto_transport._send_rtp(data)
    assert len(encrypted) == 2 and crypto_state['rtxPacketsStamped'] == 1

    # Exercise the exact object alias used by the pinned retransmitter. The
    # cached source and RTX share one extensions object; refreshing the wire
    # stamp must not overwrite that original object's saved values.
    mapping = HeaderExtensionsMap()
    mapping.configure(parameters())
    history_packet = RtpPacket(payload_type=102, sequence_number=60,
                               timestamp=90000, ssrc=123, payload=b'cached-payload')
    history_packet.extensions.abs_send_time = 42
    cached_bytes = history_packet.serialize(mapping)
    alias = wrap_rtx(history_packet,payload_type=103,sequence_number=61,ssrc=456)
    assert alias.extensions is history_packet.extensions
    alias_bytes = alias.serialize(mapping)
    await crypto_transport._send_rtp(alias_bytes)
    assert history_packet.serialize(mapping) == cached_bytes
    assert history_packet.extensions.abs_send_time == alias.extensions.abs_send_time == 42
    decoded_alias = RtpPacket.parse(encrypted[-1],mapping)
    assert decoded_alias.extensions.abs_send_time == ((now[0] >> 14) & 0xFFFFFF)
    assert decoded_alias.timestamp == 90000 and decoded_alias.payload[2:] == history_packet.payload
    await crypto_sender.stop()
    print('Verified post-pacer departure time, original/RTX SSRCs, negotiated IDs, shared transports, CSRC offsets, both extension formats, unknown bytes, missing/malformed safety, cleanup and actual SRTP authentication. No network or desktop IO.')


asyncio.run(main())
