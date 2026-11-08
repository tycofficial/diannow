"""Delayed status messages must not change actual frame-rate reporting."""
from collections import deque
from types import SimpleNamespace
from rtc_agent import DesktopSession

session=DesktopSession.__new__(DesktopSession)
track=SimpleNamespace(sent=601,frame_samples=deque((10+i/60,i+1) for i in range(601)))
assert session.recent_fps(track,20)==60
# No intervening heartbeat: the track itself remains the source of truth.
track.frame_samples.extend((20+i/60,601+i) for i in range(1,601))
track.sent=1201
assert session.recent_fps(track,30)==60
# Stopped production is counted in the observation window, never hidden by
# calculating solely between the last two old frames.
assert 35<=session.recent_fps(track,32)<=37
assert session.recent_fps(track,36)==0
assert session.recent_fps(SimpleNamespace(frame_samples=deque()),40)==0
print('Verified frame-based FPS across delayed heartbeats and actual stalls; no network or input.')
