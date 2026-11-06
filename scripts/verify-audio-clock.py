"""Captured samples drive timing, preserve gaps and wait instead of fabricating silence."""
import asyncio,queue,numpy as np,threading,time
from desktop_audio import DesktopAudioTrack
async def main():
 track=DesktopAudioTrack.__new__(DesktopAudioTrack);track.rate=48000;track.channels=2;track.samples=240;track.timestamp=0;track.closed=False;track.frames=queue.Queue(maxsize=2)
 def enqueue(value,stamp):track.frames.put_nowait((np.full(480,value,dtype=np.int16).tobytes(),stamp))
 enqueue(11,0);enqueue(22,240)
 first=await track.recv();second=await track.recv();assert (first.pts,second.pts)==(0,240)
 assert first.to_ndarray()[0,0]==11 and second.to_ndarray()[0,0]==22
 enqueue(44,12000);resumed=await track.recv();assert resumed.pts==12000 and resumed.to_ndarray()[0,0]==44
 pending=asyncio.create_task(track.recv());await asyncio.sleep(.03);assert not pending.done(),'An empty device queue must not create silent packets'
 enqueue(55,12240);following=await asyncio.wait_for(pending,1);assert following.pts==12240
 track.closed=True
 print('Capture-driven audio clock, genuine sample gaps and waiting without synthetic silence passed.')
asyncio.run(main())
