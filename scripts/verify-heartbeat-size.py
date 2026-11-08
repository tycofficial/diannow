"""Routine status must not fragment just to transport unused diagnostics."""
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch
from rtc_agent import DesktopSession

session=DesktopSession.__new__(DesktopSession)
session.track=SimpleNamespace(sent=60,width=1920,height=1080,fps=60)
session.video_enabled=True;session.fps_samples=deque()
session.source_width=1920;session.source_height=1080;session.refresh=120
session.input=SimpleNamespace(error=None,native_touch=True,cursor=lambda:{'x':.2,'y':.3,'visible':True})
session.owner=SimpleNamespace(display={'virtual':True,'name':'display-test','bounds':[1920,0,1920,1080]},audio_router=SimpleNamespace(ready=True,error=None,microphone_ready=True))
session.audio_track=SimpleNamespace(independent=True,error=None,recoveries=1)
session.audio_error=None;session.mic_sink=SimpleNamespace(device=1,error=None,recoveries=2)
with patch('rtc_agent.displays',side_effect=AssertionError('Routine heartbeat must not enumerate displays')):
    session.video_diagnostics=lambda:(_ for _ in ()).throw(AssertionError('Routine heartbeat must not build diagnostics'))
    data=session.pong({'time':1234.567})
value=json.loads(data)
assert len(data.encode())<900, 'Healthy heartbeat must fit one SCTP fragment with transport overhead'
assert value['type']=='pong' and value['time']==1234.567
assert value['microphoneReady'] and value['audioIndependent'] and value['audio']
assert 'relativePointer' in value['inputFeatures'] and value['display']['virtual']
assert value['source']['height']==1080 and value['stream']['fps']==60
assert 'videoDiagnostics' not in value and 'displays' not in value
session.video_diagnostics=lambda:{'encoderWorker':{'failed':0},'capture':{'recoveries':0}}
with patch('rtc_agent.displays',return_value=[session.owner.display]):
    detailed=json.loads(session.pong({'time':12,'diagnostics':True}))
assert detailed['videoDiagnostics']['encoderWorker']['failed']==0 and detailed['displays']
assert detailed['display']['mode']=='virtual' and detailed['display']['height']==1080
assert detailed['audioCaptureRecoveries']==1 and detailed['microphoneRecoveries']==2
session.audio_error='Audio endpoint unavailable';session.input.error='Input unavailable'
with patch('rtc_agent.displays',side_effect=AssertionError('No blocking enumeration')):
    failed=json.loads(session.pong({'time':12}))
assert failed['audioError']==session.audio_error and failed['inputError']==session.input.error
print(json.dumps({'routineHeartbeatBytes':len(data.encode()),'diagnosticsOptIn':True,'mediaAndInputStatusPreserved':True,'networkTraffic':False}))
