"""Exercise real frame pacing without capturing or transmitting the desktop."""
import asyncio
import json
import time

from rtc_agent import DesktopTrack


class SyntheticTrack(DesktopTrack):
    def __init__(self, conversion_seconds):
        super().__init__(None, 1280, 720, 60)
        self.conversion_seconds=conversion_seconds
        self.started=[]
        self.active=0
        self.maximum_active=0

    def read_frame(self):
        self.active+=1
        self.maximum_active=max(self.maximum_active,self.active)
        self.started.append(time.perf_counter())
        try:
            time.sleep(self.conversion_seconds)
            return len(self.started)
        finally:
            self.active-=1


async def measure(conversion_seconds,count):
    track=SyntheticTrack(conversion_seconds)
    try:
        for _ in range(count):
            await track.recv()
            # aiortc encodes only after recv returns. Represent its existing
            # encoder work so the pacing test includes both stages.
            await asyncio.sleep(.003)
        elapsed=track.started[-1]-track.started[0]
        fps=(count-1)/elapsed
        assert track.maximum_active==1,'Frame work accumulated concurrently.'
        assert len(track.started)==count,'Frames were prefetched into a backlog.'
        return fps,track
    finally:
        track.stop()


async def main():
    normal,track=await measure(.008,50)
    assert 53<=normal<=65,f'8ms conversion must fit the 60fps budget, got {normal:.1f}fps'
    slow,_=await measure(.040,10)
    assert slow<27,'Slow conversion must not build a queue to chase missed frames.'
    print(json.dumps({'synthetic60Fps':round(normal,1),'slowConversionFps':round(slow,1),'maxConcurrentFrameReads':track.maximum_active,'desktopCaptured':False,'networkTraffic':False}))


asyncio.run(main())
