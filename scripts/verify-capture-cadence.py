"""A timer-paced camera must not acquire another drifting sleep schedule."""
import json
from unittest.mock import patch
import numpy as np
import rtc_agent as module

class Clock:
    def __init__(self):self.now=0.;self.sleeps=[]
    def read(self):return self.now
    def sleep(self,delay):
        # Model the OS waking a sleep one millisecond after its deadline.
        self.sleeps.append(delay);self.now+=delay+.001

class PacedCamera:
    is_capturing=True
    def __init__(self,clock,count=240):self.clock=clock;self.count=count;self.times=[];self.tick=-1;self.source=None
    def get_latest_frame(self):
        # DXcam's buffered-frame event blocks until a NEW producer tick. If a
        # consumer is late, only the latest producer tick is returned.
        available=int((self.clock.now+1e-9)*60)
        self.tick=max(self.tick+1,available)
        self.clock.now=max(self.clock.now,self.tick/60)
        self.times.append(self.clock.now)
        self.clock.now+=.0002 # caller-owned frame copy
        if len(self.times)==self.count:self.source.closed=True
        return np.zeros((4,4,4),dtype=np.uint8)

clock=Clock();camera=PacedCamera(clock);source=module.SharedFrameSource(camera,60,clock=clock.read);camera.source=source
with patch.object(module.time,'perf_counter',clock.read),patch.object(module.time,'sleep',clock.sleep):source._capture()
elapsed=camera.times[-1]-camera.times[0];fps=(len(camera.times)-1)/elapsed
assert not clock.sleeps,'The already-paced capture producer must not be paced a second time'
assert abs(fps-60)<.001 and source.generation==240
assert all(abs((later-earlier)-1/60)<1e-8 for earlier,later in zip(camera.times,camera.times[1:])), 'The reader must not skip producer frames because of sleep overshoot'
assert source.raw is not None and not source.raw.flags.writeable
print(json.dumps({'syntheticProducerFps':round(fps,3),'frames':source.generation,'additionalSleeps':len(clock.sleeps),'desktopCaptured':False,'networkTraffic':False}))
