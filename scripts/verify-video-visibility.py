"""Background video suspends only its own producer, never audio or input."""
import contextlib
import io
from types import SimpleNamespace
from rtc_agent import DesktopSession

states=[]
session=DesktopSession.__new__(DesktopSession)
session.pc=SimpleNamespace(connectionState='connected');session.closed=False
session.track=SimpleNamespace(set_streaming=states.append,sent=120,readyState='live',delivery=SimpleNamespace(receiver_feedback=lambda *args,**kwargs:None))
session.video_sender=SimpleNamespace(_RTCRtpSender__rtp_task=SimpleNamespace(done=lambda:False),_RTCRtpSender__packet_count=200)
session.video_enabled=True;session.media_progress=((120,200),0)
session.set_video_enabled(False)
assert states==[False] and session.media_progress is None
assert session.media_failure(now=1000) is None,'Intentional pause is not a dead media worker'
session.set_video_enabled(True)
assert states[-1] is True and session.media_failure(now=1001) is None
assert session.media_failure(now=1009)=='video-sender-stalled','Unchanged visible state must not reset its health deadline'
session.media_progress=((120,200),0);session.set_video_enabled(True)
assert session.media_progress==((120,200),0)
session.last_report_log=0;session.session_id='visibility-test'
session.track.delivery_diagnostics=lambda:{}
with contextlib.redirect_stdout(io.StringIO()):
    session.report_receiver({'receiverReport':{'viewerVisible':0,'videoPaused':1}},now=10)
assert not session.video_enabled and states[-1] is False,'Version16 hidden reports must also stop wasted video encoding'
with contextlib.redirect_stdout(io.StringIO()):
    session.report_receiver({'receiverReport':{'viewerVisible':0,'videoRequestedEnabled':1}},now=16)
assert session.video_enabled and states[-1] is True,'An explicit requested state takes precedence over visibility'
print('Verified per-session video suspension, hidden legacy compatibility, resume grace and persistent visible-stall detection. No desktop or network IO.')
