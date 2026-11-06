"""An old prepared frame is replaced without inflating delivered FPS."""
import asyncio,time
from types import SimpleNamespace
import av
from rtc_agent import DesktopTrack

async def main():
    track=DesktopTrack(None,64,48,60,frame_source=SimpleNamespace())
    old=av.VideoFrame(64,48,'yuv420p');old.opaque={'preparedAt':time.perf_counter()-1}
    pending=asyncio.get_running_loop().create_future();pending.set_result(old);track.prefetch=pending
    def fresh():
        frame=av.VideoFrame(64,48,'yuv420p');frame.opaque={'preparedAt':time.perf_counter()};return frame
    track.read_frame=fresh
    try:
        actual=await track.recv();assert actual is not old and track.sent==1
        assert time.perf_counter()-actual.opaque['preparedAt']<.05
    finally:track.stop()
    print('Verified stale prefetch replacement and actual delivery FPS accounting. No desktop capture or network.')
asyncio.run(main())
