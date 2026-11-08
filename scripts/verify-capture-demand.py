"""A 60fps viewer must not copy a 120Hz desktop twice as often as it needs."""
from types import SimpleNamespace
from capture_recovery import RecoveringCamera
from rtc_agent import DesktopAgent

class Camera:
    is_capturing=False
    is_released=False
    def __init__(self):self.starts=[];self.stops=0
    def start(self,**options):self.starts.append(options);self.is_capturing=True
    def stop(self):self.stops+=1;self.is_capturing=False
    def release(self):self.stop();self.is_released=True

raw=Camera();camera=RecoveringCamera(lambda:raw)
agent=DesktopAgent.__new__(DesktopAgent)
agent.camera=camera;agent.refresh=120;agent.sessions=[];agent.frame_source=SimpleNamespace(refresh=120)
agent.ensure_capture_rate(60)
assert raw.starts==[{'target_fps':60,'video_mode':True}]
assert agent.frame_source.refresh==60
for _ in range(50):agent.ensure_capture_rate(60)
assert len(raw.starts)==1 and raw.stops==0
agent.sessions=[SimpleNamespace(closed=False,track=SimpleNamespace(fps=60))]
agent.ensure_capture_rate(30)
assert len(raw.starts)==1, 'A second slower viewer cannot lower the shared producer'
agent.ensure_capture_rate(120)
assert raw.starts[-1]['target_fps']==120 and raw.stops==1
assert camera.diagnostics['generation']==1 and camera.diagnostics['recoveries']==0
agent.ensure_capture_rate(60)
assert len(raw.starts)==2, 'Never cycle an active producer on lower-rate requests'
camera.stop();agent.sessions=[];agent.ensure_capture_rate(60)
assert raw.starts[-1]['target_fps']==60 and len(raw.starts)==3
camera.release()
print('Verified 60fps demand on a 120Hz monitor, shared-viewer maximum, no feedback restarts, explicit 120fps upgrade and fresh idle restart. No desktop or network IO.')
