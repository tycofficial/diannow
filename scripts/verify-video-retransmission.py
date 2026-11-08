"""Exercise long-RTT NACK recovery without capturing or sending network data."""
import asyncio
import json
from types import SimpleNamespace

from aiortc import RTCRtpSender
from aiortc.rtp import RTP_HISTORY_SIZE, RtpPacket, unwrap_rtx
from aiortc.rtcrtpparameters import RTCRtpParameters, RTCRtpHeaderExtensionParameters
from encoded_pipeline import install_encoded_pipeline
from video_retransmission import install_video_retransmission


class Clock:
    def __init__(self):
        self.now = 0
    def __call__(self):
        return self.now


def sender(kind='video', send=None):
    sent = []
    async def record(data):
        sent.append(data)
    value = RTCRtpSender(kind, SimpleNamespace(state='new', _send_rtp=send or record))
    value._RTCRtpSender__rtp_header_extensions_map.configure(RTCRtpParameters(
        headerExtensions=[RTCRtpHeaderExtensionParameters(
            id=4, uri='urn:ietf:params:rtp-hdrext:sdes:mid')]))
    return value, sent


def packet(sequence, size=1200):
    value = RtpPacket(payload_type=102, sequence_number=sequence,
                      timestamp=90000, ssrc=321, payload=bytes(size), marker=1)
    value.extensions.mid = 'desktop-video'
    return value


def write(value, sequence, size=1200):
    # This is the unmodified aiortc 1.15 _run_rtp write convention.
    value._RTCRtpSender__rtp_history[sequence % RTP_HISTORY_SIZE] = packet(sequence, size)


def parsed(value, data):
    return RtpPacket.parse(data, value._RTCRtpSender__rtp_header_extensions_map)


async def request(value, sequence):
    await value._retransmit(sequence)
    for _ in range(20):
        if not value._video_retransmission['pendingRetransmits']:return
        await asyncio.sleep(0)
    raise AssertionError('Unblocked repair worker did not drain')


async def main():
    clock = Clock()
    video, sent = sender()
    state = install_video_retransmission(video, clock=clock)
    assert install_video_retransmission(video) is state
    for sequence in range(800):
        clock.now = sequence / 800
        write(video, sequence)
    await request(video, 7)
    recovered = parsed(video, sent[-1])
    assert recovered.sequence_number == 7 and recovered.ssrc == 321
    assert recovered.payload == bytes(1200) and recovered.extensions.mid == 'desktop-video'
    assert state['nackHits'] == state['retransmittedPackets'] == 1
    assert len(video._RTCRtpSender__rtp_history) == 800
    await request(video, 1000)
    assert len(sent) == 1 and state['nackMisses'] == 1
    assert RTP_HISTORY_SIZE == 128, 'No global aiortc history limit may change'

    coalesced, coalesced_bytes = sender()
    duplicate_state = install_video_retransmission(coalesced, clock=clock)
    coalesced._RTCRtpSender__rtt = .6
    write(coalesced, 12)
    await request(coalesced, 12)
    for _ in range(100):
        await request(coalesced, 12)
    assert len(coalesced_bytes) == 1 and duplicate_state['nackSuppressed'] == 100
    clock.now += .59
    await request(coalesced, 12)
    assert len(coalesced_bytes) == 1, 'Outstanding RTX must not repeat before its RTT'
    clock.now += .02
    await request(coalesced, 12)
    assert len(coalesced_bytes) == 2, 'A genuinely lost RTX can be requested again after RTT'
    clock.now += 3.01
    history = coalesced._RTCRtpSender__rtp_history
    assert len(history) == 0 and not history.last_retransmit, 'Cooldown metadata must expire with bounded history'
    await coalesced.stop()

    bounded, _ = sender()
    limits = install_video_retransmission(bounded, max_packets=4, clock=clock)
    for sequence in range(10):
        write(bounded, sequence)
    assert len(bounded._RTCRtpSender__rtp_history) == 4
    assert limits['historyEvictions'] == 6
    clock.now += 3.01
    await request(bounded, 9)
    assert limits['nackMisses'] == 1 and limits['historyExpired'] == 4
    assert limits['historyPackets'] == limits['historyBytes'] == 0

    small, _ = sender()
    size_state = install_video_retransmission(small, max_bytes=900, clock=clock)
    for sequence in range(3):
        write(small, sequence, 300)
    assert size_state['historyPackets'] == 1 and size_state['historyBytes'] <= 900
    write(small, 4, 1000)
    assert size_state['historyOversizeDrops'] == 1

    wrapping, wrapped_bytes = sender()
    install_video_retransmission(wrapping, clock=clock)
    for sequence in [65534, 65535, 0, 1]:
        write(wrapping, sequence, 17)
    wrapping._RTCRtpSender__rtx_payload_type = 103
    wrapping._RTCRtpSender__rtx_sequence_number = 65535
    await request(wrapping, 65534)
    await request(wrapping, 0)
    first, second = [parsed(wrapping, raw) for raw in wrapped_bytes]
    assert first.payload_type == second.payload_type == 103
    assert first.ssrc == second.ssrc == wrapping._rtx_ssrc
    assert [first.sequence_number, second.sequence_number] == [65535, 0]
    assert wrapping._RTCRtpSender__rtx_sequence_number == 1
    assert unwrap_rtx(first, 102, 321).sequence_number == 65534
    assert unwrap_rtx(second, 102, 321).sequence_number == 0
    assert first.timestamp == 90000 and first.marker == 1
    assert first.extensions.mid == 'desktop-video'
    assert wrapping._RTCRtpSender__rtp_history[65534].payload == bytes(17)

    audio, _ = sender('audio')
    audio_history = audio._RTCRtpSender__rtp_history
    assert install_video_retransmission(audio) is None
    assert audio._RTCRtpSender__rtp_history is audio_history

    expiring, _ = sender()
    expires = install_video_retransmission(expiring, max_age=.01)
    write(expiring, 1)
    await asyncio.sleep(.04)
    assert expires['historyPackets'] == 0, 'Idle history must expire without future NACKs'

    entered = asyncio.Event()
    async def blocked(data):
        entered.set()
        await asyncio.Future()
    stopping, _ = sender(send=blocked)
    stop_calls = []
    original_stop = stopping.stop
    async def other_wrapper():
        stop_calls.append('other')
        await original_stop()
    stopping.stop = other_wrapper
    stop_state = install_video_retransmission(stopping)
    write(stopping, 1)
    nack = asyncio.create_task(stopping._retransmit(1))
    await asyncio.wait_for(entered.wait(), .5)
    assert stop_state['pendingRetransmits'] == 1
    await asyncio.wait_for(stopping.stop(), .5)
    await asyncio.wait_for(nack, .5)
    assert not nack.cancelled(), 'Stopping must not cancel the DTLS receive caller'
    assert stop_calls == ['other']
    assert stop_state['closed'] and stop_state['pendingRetransmits'] == 0
    assert stop_state['historyPackets'] == stop_state['historyBytes'] == 0
    write(stopping, 2)
    await request(stopping, 2)
    assert stop_state['historyPackets'] == stop_state['retransmittedPackets'] == 0

    entered.clear()
    cancelled, _ = sender(send=blocked)
    cancelled_state = install_video_retransmission(cancelled)
    write(cancelled, 1)
    caller = asyncio.create_task(cancelled._retransmit(1))
    await asyncio.wait_for(entered.wait(), .5)
    await asyncio.wait_for(caller,.02)
    assert not caller.cancelled(), 'The RTCP caller must finish while a repair is blocked'
    for sequence in range(2,1002):
        write(cancelled,sequence)
        await cancelled._retransmit(sequence)
    assert cancelled_state['maximumPendingRetransmits']==256
    assert cancelled_state['repairQueueDrops']==745
    assert cancelled_state['pendingRetransmits']==256
    await cancelled.stop()
    assert cancelled_state['historyPackets'] == cancelled_state['historyBytes'] == cancelled_state['pendingRetransmits'] == 0

    # The other install order must compose too (pipeline wrapping this stop).
    outer_original = video.stop
    async def outer_wrapper():
        stop_calls.append('outer')
        await outer_original()
    video.stop = outer_wrapper
    for value in [video, bounded, small, wrapping, expiring, audio]:
        await value.stop()
    assert stop_calls == ['other', 'outer'] and state['historyPackets'] == 0

    for history_first in [True, False]:
        combined, _ = sender()
        if history_first:
            combined_state = install_video_retransmission(combined)
            pipeline = install_encoded_pipeline(combined)
        else:
            pipeline = install_encoded_pipeline(combined)
            combined_state = install_video_retransmission(combined)
        write(combined, 7)
        await request(combined, 7)
        await combined.stop()
        assert pipeline.closed and not pipeline.tasks
        assert combined_state['closed'] and combined_state['historyPackets'] == 0
    print(json.dumps({'olderThan128Recovered': True, 'countByteAgeBounds': True,
                      'sequenceAndRtxWrap': True, 'negotiatedExtensionsPreserved': True,
                      'audioUnchanged': True, 'stopComposesAndCancelsOwnedSends': True,
                      'rtcpProgressWhileRepairBlocked': True,
                      'repairQueueBoundedAt256': True,
                      'duplicateNacksCoalescedUntilRtt': True,
                      'networkTraffic': False, 'desktopCaptured': False}))


asyncio.run(main())
