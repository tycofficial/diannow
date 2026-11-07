"""Keep a bounded video NACK history long enough for a distant receiver.

aiortc 1.15 stores packets at sequence_number % 128. At 8 Mbps that can
overwrite a packet before a 600 ms NACK reaches the desktop. Adapt only the
selected video sender's history writes and retransmission method; retain the
library's RTX format, sequence arithmetic and negotiated header extensions.
"""
import asyncio
import time
from collections import OrderedDict
from collections.abc import MutableMapping

from aiortc.rtp import wrap_rtx
from aiortc.utils import uint16_add


class VideoPacketHistory(MutableMapping):
    """Accept modulo-slot writes, index packets by their actual 16-bit seq."""
    def __init__(self, state, max_packets, max_age, max_bytes, clock):
        self.state = state
        self.max_packets = max_packets
        self.max_age = max_age
        self.max_bytes = max_bytes
        self.clock = clock
        self.packets = OrderedDict()
        self.last_retransmit = {}
        self.timer = None
        self.closed = False

    def _remove(self, sequence_number):
        _, packet, size = self.packets.pop(sequence_number)
        self.last_retransmit.pop(sequence_number, None)
        self.state['historyBytes'] -= size
        self.state['historyPackets'] = len(self.packets)
        return packet

    def prune(self):
        cutoff = self.clock() - self.max_age
        while self.packets:
            sequence_number, (stored_at, _, _) = next(iter(self.packets.items()))
            if stored_at > cutoff:
                break
            self._remove(sequence_number)
            self.state['historyExpired'] += 1

    def _schedule_expiry(self):
        if self.closed or self.timer is not None or not self.packets:
            return
        stored_at = next(iter(self.packets.values()))[0]
        delay = max(0, stored_at + self.max_age - self.clock())
        self.timer = asyncio.get_running_loop().call_later(delay, self._expire)

    def _expire(self):
        self.timer = None
        self.prune()
        self._schedule_expiry()

    def __setitem__(self, modulo_slot, packet):
        if self.closed:
            return
        self.prune()
        sequence_number = packet.sequence_number
        # Payload accounts for most storage; reserve header/extension space.
        size = len(packet.payload) + 256
        if sequence_number in self.packets:
            self._remove(sequence_number)
        if size > self.max_bytes:
            self.state['historyOversizeDrops'] += 1
            return
        self.packets[sequence_number] = (self.clock(), packet, size)
        self.state['historyBytes'] += size
        self.state['historyPackets'] = len(self.packets)
        while len(self.packets) > self.max_packets or self.state['historyBytes'] > self.max_bytes:
            self._remove(next(iter(self.packets)))
            self.state['historyEvictions'] += 1
        self._schedule_expiry()

    def __getitem__(self, sequence_number):
        self.prune()
        return self.packets[sequence_number][1]

    def __delitem__(self, sequence_number):
        self._remove(sequence_number)

    def allow_retransmit(self, sequence_number, cooldown):
        now = self.clock()
        previous = self.last_retransmit.get(sequence_number)
        if previous is not None and now - previous < cooldown:
            return False
        self.last_retransmit[sequence_number] = now
        return True

    def __iter__(self):
        self.prune()
        return iter(self.packets)

    def __len__(self):
        self.prune()
        return len(self.packets)

    def clear(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        self.packets.clear()
        self.last_retransmit.clear()
        self.state['historyBytes'] = 0
        self.state['historyPackets'] = 0

    def close(self):
        self.closed = True
        self.clear()


def install_video_retransmission(sender, max_packets=4096, max_age=3.0,
                                 max_bytes=8 * 1024 * 1024, *, clock=time.monotonic):
    """Return serializable counters; leave audio and unsupported senders alone.

    Install before starting the sender. stop() composes with other per-sender
    wrappers, and cancels only sends created here, never the DTLS receive task
    that called _retransmit(). History is bounded by count, age and bytes.
    """
    existing = getattr(sender, '_video_retransmission', None)
    if existing is not None:
        return existing
    if getattr(sender, 'kind', None) != 'video':
        return None
    required = ['_RTCRtpSender__rtp_history', '_RTCRtpSender__rtp_header_extensions_map',
                '_RTCRtpSender__rtx_payload_type', '_RTCRtpSender__rtx_sequence_number']
    if not all(hasattr(sender, name) for name in required):
        return None
    if not isinstance(max_packets, int) or not 1 <= max_packets <= 4096:
        raise ValueError('max_packets must be between 1 and 4096')
    if not 0 < max_age <= 3:
        raise ValueError('max_age must be positive and at most three seconds')
    if not isinstance(max_bytes, int) or not 1 <= max_bytes <= 8 * 1024 * 1024:
        raise ValueError('max_bytes must be positive and at most eight MiB')

    state = {'historyLimit': max_packets, 'historyMaxAgeMs': max_age * 1000,
             'historyByteLimit': max_bytes, 'historyPackets': 0, 'historyBytes': 0,
             'historyExpired': 0, 'historyEvictions': 0, 'historyOversizeDrops': 0,
             'nackHits': 0, 'nackMisses': 0, 'nackSuppressed': 0, 'retransmittedPackets': 0,
             'pendingRetransmits': 0, 'closed': False}
    history = VideoPacketHistory(state, max_packets, max_age, max_bytes, clock)
    previous = sender._RTCRtpSender__rtp_history
    for packet in previous.values():
        history[packet.sequence_number] = packet
    sender._RTCRtpSender__rtp_history = history
    active_sends = set()
    original_stop = sender.stop

    async def retransmit(sequence_number):
        if state['closed']:
            return
        packet = history.get(sequence_number)
        if packet is None:
            state['nackMisses'] += 1
            return
        state['nackHits'] += 1
        # A receiver repeats outstanding gaps in later NACKs before the first
        # RTX can complete a round trip. Sending each duplicate immediately
        # multiplies traffic and can starve the original frames and ICE.
        rtt = getattr(sender, '_RTCRtpSender__rtt', None)
        cooldown = max(.05, min(1.0, rtt)) if isinstance(rtt, (int, float)) and 0 < rtt < 60 else .2
        if not history.allow_retransmit(sequence_number, cooldown):
            state['nackSuppressed'] += 1
            return
        if sender._RTCRtpSender__rtx_payload_type is not None:
            packet = wrap_rtx(packet,
                              payload_type=sender._RTCRtpSender__rtx_payload_type,
                              sequence_number=sender._RTCRtpSender__rtx_sequence_number,
                              ssrc=sender._rtx_ssrc)
            sender._RTCRtpSender__rtx_sequence_number = uint16_add(
                sender._RTCRtpSender__rtx_sequence_number, 1)
        log = getattr(sender, '_RTCRtpSender__log_debug', None)
        if log is not None:
            log('> %s', packet)
        packet_bytes = packet.serialize(sender._RTCRtpSender__rtp_header_extensions_map)
        task = asyncio.create_task(sender.transport._send_rtp(packet_bytes))
        active_sends.add(task)
        state['pendingRetransmits'] = len(active_sends)
        try:
            await task
            state['retransmittedPackets'] += 1
        except asyncio.CancelledError:
            # stop() cancels our child send without cancelling its DTLS
            # caller. A real cancellation of that caller must still escape,
            # including when transport shutdown already marked us closed.
            if not state['closed'] or asyncio.current_task().cancelling():
                raise
        finally:
            active_sends.discard(task)
            state['pendingRetransmits'] = len(active_sends)

    async def stop(*args, **kwargs):
        state['closed'] = True
        history.close()
        pending = list(active_sends)
        for task in pending:
            task.cancel()
        try:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            return await original_stop(*args, **kwargs)
        finally:
            history.close()

    sender._retransmit = retransmit
    sender.stop = stop
    sender._video_retransmission = state
    return state
