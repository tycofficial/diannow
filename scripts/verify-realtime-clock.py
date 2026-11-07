"""Verify scoped high-resolution deadlines and Runner resource cleanup."""
import asyncio
import time
from realtime_clock import run_realtime,realtime_loop_factory


loop=realtime_loop_factory()
try:
    assert loop._clock_resolution==time.get_clock_info('perf_counter').resolution
    assert abs(loop.time()-time.perf_counter())<.002
finally:loop.close();asyncio.set_event_loop(None)

active=[]
async def exercise():
    loop=asyncio.get_running_loop();active.append(loop)
    assert abs(loop.time()-time.perf_counter())<.002
    completed=loop.create_future();when=loop.time()+.006
    loop.call_at(when,lambda:completed.set_result(loop.time()))
    fired=await completed
    assert fired>=when-.0001,'A callback cannot fire one coarse clock tick early'
    return 42
assert run_realtime(exercise())==42
assert active[0].is_closed(),'Runner must close its scoped event loop'
print('Verified QPC-based deadlines, callback ordering and loop cleanup. No desktop or network IO.')
