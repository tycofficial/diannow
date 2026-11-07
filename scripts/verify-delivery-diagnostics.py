"""Exercise delivery diagnostics using deterministic counters only."""
import contextlib
import io
import json
import time
from rtc_agent import DesktopTrack,DesktopSession

track=DesktopTrack(None,1280,720,60,'diagnostic-test')
try:
    track.delivery.fps=20;track.delivery.bandwidth=4000000;track.delivery.frame_bytes=20480
    track.frame_samples.extend([(0,0),(6,100),(7,120),(8,140),(9,160),(10,180)])
    values=track.delivery_diagnostics(now=10)
    assert values=={'deliveryFps':20,'estimatedMbps':4.0,'avgFrameKB':20.0,'actualFps':20.0,'deliveryTargetFps':60,'deliveryReason':'target-no-confirmed-congestion'}
    now=time.perf_counter();track.frame_samples.clear();track.frame_samples.extend([(now-2,10),(now,50)])
    session=DesktopSession.__new__(DesktopSession);session.session_id='diagnostic-test';session.last_report_log=0;session.track=track
    output=io.StringIO()
    with contextlib.redirect_stdout(output):session.report_receiver({'receiverReport':{'fps':19.8,'bufferMs':34}},now=10)
    report=json.loads(output.getvalue())
    assert report['deliveryFps']==60 and report['estimatedMbps']==4.0 and report['avgFrameKB']==20.0 and report['actualFps']==20.0
    assert report['receiverReport']=={'fps':19.8,'bufferMs':34}
    report_values={'fps':60,'receivedFps':60,'presentedFps':15,'videoReceivedDelta':60,'videoPresentedDelta':15,'videoPresentationSource':1,'videoPresentationAgeMs':120,'viewerVisible':1,'videoPaused':0}
    output=io.StringIO()
    with contextlib.redirect_stdout(output):session.report_receiver({'receiverReport':report_values},now=20)
    assert json.loads(output.getvalue())['receiverReport']==report_values,'Decoded, received and actually presented frames must remain distinguishable in logs'
    print('Delivery diagnostics verified: recent actual fps, REMB estimate, frame size and pacing cap. No desktop or network IO.')
finally:track.stop()
