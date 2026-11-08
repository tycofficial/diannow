"""Give each aiortc sender a serial encoder worker independent of other IO.

Pinned aiortc 1.15 calls its private loop only for run_in_executor(None,
encoder.encode, ...). A per-instance loop delegate keeps that implementation
and routes its encode job to a dedicated worker. No global class or loop is
changed, and explicit executors continue to use the real event loop.
"""
import asyncio
import concurrent.futures
import threading


class EncoderWorker:
    def __init__(self, loop, kind):
        self.loop = loop
        self.pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='Encode-' + kind)
        self.lock = threading.Lock()
        self.pending = None
        self.closed = False
        self.stats = {'submitted': 0, 'completed': 0, 'failed': 0,
                      'active': 0, 'maximumOutstanding': 0, 'closed': False}

    def __getattr__(self, name):
        return getattr(self.loop, name)

    def run_in_executor(self, executor, function, *args):
        if executor is not None:
            return self.loop.run_in_executor(executor, function, *args)
        with self.lock:
            if self.closed:
                raise RuntimeError('Media encoder worker is closed')
            # aiortc and EncodedPipeline both submit serially. Refuse an
            # accidental second encode instead of accumulating an unbounded
            # executor queue or calling one codec from two threads.
            if self.pending is not None and not self.pending.done():
                raise RuntimeError('Concurrent jobs for one media encoder')
            future = self.pool.submit(function, *args)
            self.pending = future
            self.stats['submitted'] += 1
            self.stats['active'] = 1
            self.stats['maximumOutstanding'] = 1
        future.add_done_callback(self._finished)
        return asyncio.wrap_future(future, loop=self.loop)

    def _finished(self, future):
        with self.lock:
            if self.pending is future:
                self.pending = None
                self.stats['active'] = 0
            if not future.cancelled():
                self.stats['completed'] += 1
                self.stats['failed'] += int(future.exception() is not None)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.stats['closed'] = True
        # A native encoder already running cannot be cancelled safely. Let
        # that single job finish without blocking ICE/input/session cleanup.
        self.pool.shutdown(wait=False, cancel_futures=True)


def install_media_encoder(sender):
    """Install before send starts; return None for an unsupported sender."""
    existing = getattr(sender, '_media_encoder_worker', None)
    if existing is not None:
        return existing
    loop = getattr(sender, '_RTCRtpSender__loop', None)
    if loop is None or getattr(sender, 'kind', None) not in ('video', 'audio'):
        return None
    worker = EncoderWorker(loop, sender.kind)
    sender._RTCRtpSender__loop = worker
    sender._media_encoder_worker = worker
    original_stop = sender.stop

    async def stop(*args, **kwargs):
        try:
            return await original_stop(*args, **kwargs)
        finally:
            worker.close()

    sender.stop = stop
    track = sender.track
    if track is not None:
        original_track_stop = track.stop

        def stop_track():
            worker.close()
            return original_track_stop()

        track.stop = stop_track
    return worker
