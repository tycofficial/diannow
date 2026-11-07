"""Incomplete peers must not consume a competing video pipeline."""
import asyncio
import json
from types import SimpleNamespace
import av
from aiortc import MediaStreamError
from rtc_agent import DesktopTrack

class SyntheticTrack(DesktopTrack):
    def __init__(self):
        super().__init__(None,64,48,60,frame_source=SimpleNamespace())
        self.reads=0
    def read_frame(self):
        self.reads+=1
        return av.VideoFrame(64,48,'yuv420p')

async def main():
    track=SyntheticTrack();track.set_streaming(False)
    pending=asyncio.create_task(track.recv())
    try:
        await asyncio.sleep(.03)
        assert not pending.done() and track.reads==track.sent==0
        assert not getattr(track,'prefetch',None) and not track.capture_pool._threads,'Handshake-only peers must not even arm the read pool'
        track.set_streaming(True)
        await asyncio.wait_for(pending,.5)
        assert track.sent==1 and track.reads>=1

        track.set_streaming(False)
        await asyncio.sleep(.02) # allow the single already-prepared frame to settle
        baseline=track.reads;pending=asyncio.create_task(track.recv())
        await asyncio.sleep(.03)
        assert not pending.done() and track.reads==baseline and track.sent==1,'A disconnected peer must stop requesting further frame work'
        track.set_streaming(True)
        await asyncio.wait_for(pending,.5)
        assert track.sent==2

        track.set_streaming(False);pending=asyncio.create_task(track.recv())
        await asyncio.sleep(.01);track.stop()
        try:await asyncio.wait_for(pending,.5)
        except MediaStreamError:pass
        else:raise AssertionError('Stopping a paused track must wake and end recv')
        try:await track.recv()
        except MediaStreamError:pass
        else:raise AssertionError('An ended track must reject future reads')
    finally:
        track.stop()
        if not pending.done():pending.cancel()
    print(json.dumps({'halfConnectedVideoReads':0,'halfConnectedReadThreads':0,'resumedOnConnected':True,'pausedOnDisconnected':True,'stopWakesPausedRecv':True,'desktopCaptured':False,'networkTraffic':False}))

asyncio.run(main())
