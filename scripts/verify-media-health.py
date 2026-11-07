"""Dead video workers must not leave an apparently connected zombie session."""
import asyncio
import json
from types import SimpleNamespace
from rtc_agent import DesktopSession,DesktopTrack

def session():
    value=DesktopSession.__new__(DesktopSession)
    value.closed=False;value.pc=SimpleNamespace(connectionState='connected')
    value.track=SimpleNamespace(readyState='live',sent=60)
    value.video_sender=SimpleNamespace(_RTCRtpSender__rtp_task=SimpleNamespace(done=lambda:False),_RTCRtpSender__packet_count=100)
    return value

active=session()
assert active.media_failure(now=0) is None
for now in range(1,20):
    active.track.sent+=60;active.video_sender._RTCRtpSender__packet_count+=120
    assert active.media_failure(now=now) is None
assert active.media_failure(now=26.9) is None
assert active.media_failure(now=27)=='video-sender-stalled','A hung capture/encoder must time out even while control remains connected'
active.video_sender._RTCRtpSender__packet_count+=1
assert active.media_failure(now=28) is None,'Packets still draining a large frame count as real progress'
active.pc.connectionState='disconnected'
assert active.media_failure(now=100) is None,'ICE recovery uses the separate disconnection grace period'
active.pc.connectionState='connected'
assert active.media_failure(now=101) is None,'A recovered transport receives a fresh progress window'
active.video_sender._RTCRtpSender__rtp_task=SimpleNamespace(done=lambda:True)
assert active.media_failure(now=101)=='video-sender-ended'
active.video_sender._RTCRtpSender__rtp_task=SimpleNamespace(done=lambda:False);active.track.readyState='ended'
assert active.media_failure(now=101)=='video-track-ended'

async def main():
    track=DesktopTrack(None,64,48,60,frame_source=SimpleNamespace())
    track.frame_timeout=.02
    track.prefetch=asyncio.get_running_loop().create_future()
    try:
        try:await asyncio.wait_for(track.recv(),.5)
        except RuntimeError as error:assert 'stalled' in str(error)
        else:raise AssertionError('A permanently pending dead-worker future must fail')
        assert track.sent==0,'Timed-out preparation must never be counted as delivered FPS'
    finally:track.stop()
    print(json.dumps({'terminalVideoSenderDetected':True,'hungVideoWorkerBoundSeconds':8,'connectedDataCannotMaskDeadVideo':True,'pendingPreparationTimedOut':True,'desktopCaptured':False,'networkTraffic':False}))

asyncio.run(main())
