"""Bound microphone backlog before and after the aiortc audio decoder.

Pinned aiortc 1.15 otherwise retains every encoded packet and decoded frame.
The sink's playback cap cannot bound either upstream queue. These per-receiver
adapters preserve the original decoder worker and never block RTP reception.
"""
import asyncio
import queue


class LatestDecoderPackets(queue.Queue):
    def __init__(self, maximum=3):
        super().__init__(maximum)
        self.dropped = 0
        self.maximum_observed = 0
        self.ended = False

    def put(self, item, block=True, timeout=None):
        # A full queue must never stall the event loop waiting for libopus.
        # Preserve the terminal sentinel even if packets arrive during stop.
        with self.not_full:
            if self.ended:
                return
            while self._qsize() and (item is None or self._qsize() >= self.maxsize):
                old = self._get()
                self.unfinished_tasks -= 1
                self.dropped += int(old is not None)
            self.ended = item is None
            self._put(item)
            self.unfinished_tasks += 1
            self.maximum_observed = max(self.maximum_observed, self._qsize())
            self.not_empty.notify()


class LatestDecodedFrames(asyncio.Queue):
    def __init__(self, maximum=3):
        super().__init__(maximum)
        self.dropped = 0
        self.maximum_observed = 0
        self.ended = False

    async def put(self, item):
        self.put_nowait(item)

    def put_nowait(self, item):
        if self.ended:
            return
        while not self.empty() and (item is None or self.full()):
            old = self.get_nowait()
            self.task_done()
            self.dropped += int(old is not None)
        self.ended = item is None
        super().put_nowait(item)
        self.maximum_observed = max(self.maximum_observed, self.qsize())


class AudioReceiveBuffers:
    def __init__(self):
        self.encoded = LatestDecoderPackets(3)
        self.decoded = LatestDecodedFrames(3)

    @property
    def stats(self):
        return {
            'encodedQueued': self.encoded.qsize(),
            'encodedDropped': self.encoded.dropped,
            'encodedMaximum': self.encoded.maximum_observed,
            'decodedQueued': self.decoded.qsize(),
            'decodedDropped': self.decoded.dropped,
            'decodedMaximum': self.decoded.maximum_observed,
        }


def install_audio_receive_buffers(receiver):
    """Call after the remote track exists and before receive/consume starts."""
    installed = getattr(receiver, '_audio_receive_buffers', None)
    if installed is not None:
        return installed
    track = receiver.track
    if track is None or track.kind != 'audio':
        return None
    if getattr(receiver, '_RTCRtpReceiver__decoder_thread', None) is not None:
        raise RuntimeError('Audio receive buffers must be installed before decoding')
    original = getattr(receiver, '_RTCRtpReceiver__decoder_queue', None)
    output = getattr(track, '_queue', None)
    if original is None or output is None:
        return None
    if not original.empty() or not output.empty() or output._getters:
        raise RuntimeError('Audio receive buffers must be installed before consuming')
    installed = AudioReceiveBuffers()
    receiver._RTCRtpReceiver__decoder_queue = installed.encoded
    track._queue = installed.decoded
    receiver._audio_receive_buffers = installed
    return installed
