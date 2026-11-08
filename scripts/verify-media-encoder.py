"""Exercise real aiortc senders while the default executor is saturated."""
import asyncio
import concurrent.futures
import fractions
import json
import threading
import time

import av
from aiortc import RTCPeerConnection, RTCConfiguration, VideoStreamTrack
from aiortc.rtcrtpparameters import RTCRtpCodecParameters
from media_encoder import EncoderWorker, install_media_encoder


class GeneratedTrack(VideoStreamTrack):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.count = 0

    async def recv(self):
        self.count += 1
        if self.kind == 'video':
            frame = av.VideoFrame(64, 48, 'yuv420p')
            frame.pts = self.count * 1500
            frame.time_base = fractions.Fraction(1, 90000)
        else:
            frame = av.AudioFrame(format='s16', layout='stereo', samples=240)
            frame.sample_rate = 48000
            frame.pts = self.count * 240
            frame.time_base = fractions.Fraction(1, 48000)
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        return frame


class EncoderDouble:
    def __init__(self, started, release):
        self.started = started
        self.release = release
        self.threads = []

    def encode(self, frame, force):
        self.threads.append(threading.current_thread().name)
        self.started.set()
        if not self.release.wait(2):
            raise TimeoutError('Synthetic encoder was not released')
        return [b'payload'], frame.pts


async def main():
    loop = asyncio.get_running_loop()
    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=1))
    default_started = threading.Event()
    default_release = threading.Event()

    def block_default():
        default_started.set()
        default_release.wait(3)

    blocked = loop.run_in_executor(None, block_default)
    while not default_started.is_set():
        await asyncio.sleep(.001)
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    workers = []
    release = threading.Event()
    try:
        senders = []
        codecs = []
        encoders = []
        for kind in ['video', 'audio']:
            sender = peer.addTrack(GeneratedTrack(kind))
            worker = install_media_encoder(sender)
            assert worker is not None and install_media_encoder(sender) is worker
            workers.append(worker)
            encoder = EncoderDouble(threading.Event(), release)
            sender._RTCRtpSender__encoder = encoder
            senders.append(sender)
            encoders.append(encoder)
            codecs.append(RTCRtpCodecParameters(
                mimeType='video/H264' if kind == 'video' else 'audio/opus',
                clockRate=90000 if kind == 'video' else 48000,
                payloadType=102 if kind == 'video' else 111))
        tasks = [asyncio.create_task(sender._next_encoded_frame(codec))
                 for sender, codec in zip(senders, codecs)]
        deadline = time.perf_counter() + 1
        while not all(encoder.started.is_set() for encoder in encoders):
            assert time.perf_counter() < deadline, 'A default executor blocker starved media'
            await asyncio.sleep(.001)
        assert not blocked.done()
        assert encoders[0].threads[0].startswith('Encode-video')
        assert encoders[1].threads[0].startswith('Encode-audio')
        release.set()
        frames = await asyncio.wait_for(asyncio.gather(*tasks), 1)
        assert all(frame.payloads == [b'payload'] for frame in frames)
        assert all(worker.stats['maximumOutstanding'] == 1 for worker in workers)

        # Disabled senders keep consuming track frames without encoding, as
        # in the original aiortc method. It is not copied or replaced here.
        senders[0]._enabled = False
        assert await senders[0]._next_encoded_frame(codecs[0]) is None
        assert len(encoders[0].threads) == 1

        # Cancellation cannot cancel a running C/native call. Its outstanding
        # slot stays occupied until it exits, avoiding a hidden queued job.
        worker = EncoderWorker(loop, 'cancellation')
        started = threading.Event()
        finish = threading.Event()
        def slow():
            started.set()
            finish.wait(1)
        future = worker.run_in_executor(None, slow)
        while not started.is_set():
            await asyncio.sleep(.001)
        future.cancel()
        await asyncio.sleep(0)
        try:
            worker.run_in_executor(None, lambda: None)
        except RuntimeError as error:
            assert 'Concurrent' in str(error)
        else:
            raise AssertionError('A cancelled caller allowed a queued encode')
        before = time.perf_counter()
        worker.close()
        assert time.perf_counter() - before < .1
        finish.set()
        while worker.stats['active']:
            await asyncio.sleep(.001)
        assert worker.stats['completed'] == 1 and worker.stats['closed']

        # Real software H264, with the global executor still occupied.
        real = peer.addTrack(GeneratedTrack('video'))
        real_worker = install_media_encoder(real)
        workers.append(real_worker)
        encoded = await asyncio.wait_for(real._next_encoded_frame(codecs[0]), 2)
        assert encoded.payloads and not blocked.done()
        real.track.stop()
        assert real_worker.closed, 'Ending a track must release its idle encoder worker'
        await real.stop()
        assert real_worker.closed
        for sender in senders:
            await sender.stop()
        assert all(worker.closed for worker in workers)
        print(json.dumps({'defaultExecutorSaturated': True,
                          'audioVideoEncodeIndependently': True,
                          'maximumOutstandingPerSender': 1,
                          'cancelledNativeJobCannotQueueMoreWork': True,
                          'shutdownDoesNotBlockEventLoop': True,
                          'realAiortcAndH264': True,
                          'desktopCaptured': False, 'networkTraffic': False}))
    finally:
        release.set()
        default_release.set()
        await blocked
        await peer.close()


asyncio.run(main())
