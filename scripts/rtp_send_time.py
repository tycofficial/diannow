"""Stamp negotiated absolute send time after pacing, before SRTP encryption.

Install on video senders BEFORE wrapping their DTLS transport with pacing or
cooperative scheduling. Wrappers installed later must await this hook only
after their queue/pacing waits. Each transport and sender is patched once;
no aiortc class or shared extension map is modified.

The wire-only edit preserves cached packets and every other extension byte.
Specification: https://webrtc.googlesource.com/src/+/refs/heads/main/docs/native-code/rtp-hdrext/abs-send-time/README.md
"""
import struct

from aiortc import clock
from aiortc.rtp import is_rtcp


ABS_SEND_TIME_URI = 'http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time'


def _extension_position(data, identifier):
    """Find one three-byte AST value without decoding/repacking other values.

    Return -1 for absent/unsupported extensions and -2 for malformed or
    ambiguous data. RFC 8285 two-byte profile application bits are retained.
    """
    if not data[0] & 0x10:
        return -1
    offset = 12 + 4 * (data[0] & 15)
    if offset + 4 > len(data):
        return -2
    profile, words = struct.unpack_from('!HH', data, offset)
    offset += 4
    end = offset + 4 * words
    if end > len(data):
        return -2
    one_byte = profile == 0xBEDE
    if not one_byte and profile & 0xFFF0 != 0x1000:
        return -1
    found = -1
    while offset < end:
        header = data[offset]
        offset += 1
        if header == 0:
            continue
        if one_byte:
            current = header >> 4
            if current == 15:
                break
            length = (header & 15) + 1
        else:
            current = header
            if offset >= end:
                return -2
            length = data[offset]
            offset += 1
        if offset + length > end:
            return -2
        if current == identifier:
            if length != 3 or found >= 0:
                return -2
            found = offset
        offset += length
    return found


class _SendTimeTransport:
    def __init__(self, transport, ntp_clock):
        self.original = transport._send_rtp
        self.ntp_clock = ntp_clock
        self.bindings = {}
        self.state = {'packetsStamped': 0, 'rtxPacketsStamped': 0,
                      'missingExtensions': 0, 'invalidExtensions': 0,
                      'negotiatedSenders': 0}
        self.senders = set()

    def bind(self, sender, parameters):
        primary, repair = sender._ssrc, sender._rtx_ssrc
        self.unbind(sender)
        identifiers = [extension.id for extension in parameters.headerExtensions
                       if extension.uri == ABS_SEND_TIME_URI]
        if len(identifiers) != 1:
            return
        identifier = identifiers[0]
        if not isinstance(identifier, int) or isinstance(identifier, bool) or not 1 <= identifier <= 255:
            return
        self.bindings[primary] = (identifier, False)
        self.bindings[repair] = (identifier, True)
        self.senders.add(primary)
        self.state['negotiatedSenders'] = len(self.senders)

    def unbind(self, sender):
        self.bindings.pop(sender._ssrc, None)
        self.bindings.pop(sender._rtx_ssrc, None)
        self.senders.discard(sender._ssrc)
        self.state['negotiatedSenders'] = len(self.senders)

    async def send(self, data):
        if len(data) >= 12 and data[0] >> 6 == 2 and not is_rtcp(data):
            binding = self.bindings.get(struct.unpack_from('!I', data, 8)[0])
            if binding is not None:
                identifier, repair = binding
                position = _extension_position(data, identifier)
                if position >= 0:
                    stamp = ((self.ntp_clock() >> 14) & 0xFFFFFF).to_bytes(3, 'big')
                    # The history contains packet objects whose extension
                    # objects can be shared by wrap_rtx(). Change only these
                    # already-serialized bytes, never the cached objects.
                    if data[position:position + 3] != stamp:
                        data = data[:position] + stamp + data[position + 3:]
                    self.state['packetsStamped'] += 1
                    self.state['rtxPacketsStamped'] += int(repair)
                else:
                    self.state['invalidExtensions' if position == -2 else 'missingExtensions'] += 1
        # No await before this call: original is the unpaced SRTP boundary.
        await self.original(data)


def install_rtp_send_time(sender, *, ntp_clock=None):
    """Install before send/pacing starts; return shared serializable counters."""
    existing = getattr(sender, '_desktop_rtp_send_time', None)
    if existing is not None:
        return existing.state
    required = ('_ssrc', '_rtx_ssrc', 'send', 'stop', 'transport')
    if getattr(sender, 'kind', None) != 'video' or not all(hasattr(sender, name) for name in required):
        return None
    transport = sender.transport
    if not hasattr(transport, '_send_rtp'):
        return None
    hook = getattr(transport, '_desktop_rtp_send_time', None)
    if hook is None:
        if hasattr(transport, '_turn_video_flow') or hasattr(transport, '_cooperative_rtp_send'):
            raise RuntimeError('Install RTP send-time stamping before pacing/cooperative wrappers')
        hook = _SendTimeTransport(transport, ntp_clock or clock.current_ntp_time)
        transport._send_rtp = hook.send
        transport._desktop_rtp_send_time = hook
    original_send, original_stop = sender.send, sender.stop

    async def send(parameters):
        # Native send() ignores a repeated invocation after it has started.
        # Such a call must not silently change the negotiated extension ID.
        if not getattr(sender, '_RTCRtpSender__started', False):
            hook.bind(sender, parameters)
        await original_send(parameters)

    async def stop(*args, **kwargs):
        try:
            return await original_stop(*args, **kwargs)
        finally:
            hook.unbind(sender)

    sender.send = send
    sender.stop = stop
    sender._desktop_rtp_send_time = hook
    return hook.state
