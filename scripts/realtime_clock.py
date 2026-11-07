"""Use the high-resolution process clock for this media event loop only."""
import asyncio
import time


def realtime_loop_factory():
    loop=asyncio.new_event_loop()
    # CPython 3.12 on this Windows host uses 15.625ms GetTickCount64 for
    # monotonic(), while perf_counter() is QueryPerformanceCounter. Keep
    # callback deadlines and the RTP/frame pacers in the same precise clock.
    loop.time=time.perf_counter
    loop._clock_resolution=time.get_clock_info('perf_counter').resolution
    asyncio.set_event_loop(loop)
    return loop


def run_realtime(coroutine):
    with asyncio.Runner(loop_factory=realtime_loop_factory) as runner:
        return runner.run(coroutine)
