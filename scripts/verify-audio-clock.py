"""Check audio delivery after a stalled sender, without opening audio devices."""
import asyncio,queue
import numpy as np
import desktop_audio as module

async def main():
    now=[10.0];sleeps=[]
    real_clock=module.time.perf_counter;real_sleep=module.asyncio.sleep
    module.time.perf_counter=lambda:now[0]
    async def sleep(delay):sleeps.append(delay);now[0]+=delay
    module.asyncio.sleep=sleep
    track=module.DesktopAudioTrack.__new__(module.DesktopAudioTrack)
    track.rate=48000;track.channels=2;track.samples=960;track.timestamp=0;track.started=None;track.frames=queue.Queue(maxsize=2)
    def enqueue(value):track.frames.put_nowait(np.full(1920,value,dtype=np.int16).tobytes())
    try:
        enqueue(11);enqueue(22)
        first=await track.recv();second=await track.recv()
        assert (first.pts,second.pts)==(0,960)
        assert first.to_ndarray()[0,0]==11 and second.to_ndarray()[0,0]==22
        assert len(sleeps)==1 and abs(sleeps[0]-.02)<.00001

        # A 250 ms sender stall leaves two bounded capture frames. Keep the
        # newest one at a current timestamp, with no burst of silent packets.
        enqueue(33);enqueue(44);now[0]+=.25
        recovered=await track.recv()
        assert recovered.pts>=12000 and recovered.pts%960==0
        assert recovered.to_ndarray()[0,0]==44 and track.frames.empty()
        enqueue(55);following=await track.recv()
        assert following.pts-recovered.pts==960
        assert following.to_ndarray()[0,0]==55
        assert len(sleeps)==2 and 0<sleeps[-1]<=.02
        assert first.pts<second.pts<recovered.pts<following.pts
        print('Audio clock checks passed: normal frames preserved; stalled capture resumes with fresh audio and no catch-up burst. No devices opened or audio transmitted.')
    finally:
        module.time.perf_counter=real_clock;module.asyncio.sleep=real_sleep

asyncio.run(main())
