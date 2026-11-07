"""Verify bounded encode/send overlap without desktop or network IO."""
import asyncio
import json
import time
import fractions
from types import SimpleNamespace
import av
from aiortc import MediaStreamError,RTCPeerConnection,RTCConfiguration,VideoStreamTrack,RTCRtpCodecParameters
from encoded_pipeline import install_encoded_pipeline
from realtime_clock import run_realtime
from rtc_agent import WindowsTimerResolution


class TrackDouble:
    def __init__(self):
        self.width=1920;self.height=1080;self.fps=60;self.fixed_profile=True;self.readyState='live'
        self.streaming=asyncio.Event();self.streaming.set()
    def stop(self):self.readyState='ended';self.streaming.set()


class SenderDouble:
    def __init__(self,encode_seconds=0,paced=False):
        self.track=TrackDouble();self.encode_seconds=encode_seconds;self.paced=paced
        self.calls=0;self.active=0;self.maximum_active=0;self.force=False;self.fail_on=None;self.block=None;self.next_capture=0
        self._RTCRtpSender__encoder=SimpleNamespace(applied_profile=self.profile(),last_keyframe_seconds=0)
    def profile(self):return self.track.width,self.track.height,self.track.fps,self.track.fixed_profile
    def _send_keyframe(self):self.force=True
    async def _next_encoded_frame(self,codec):
        await self.track.streaming.wait()
        if self.track.readyState!='live':raise MediaStreamError
        self.calls+=1;index=self.calls;self.active+=1;self.maximum_active=max(self.maximum_active,self.active)
        try:
            if self.paced:
                now=time.perf_counter();self.next_capture=max(now,self.next_capture)
                if self.next_capture>now:await asyncio.sleep(self.next_capture-now)
                self.next_capture+=1/60
            if self.block is not None:await self.block.wait()
            profile=self.profile();keyframe=self.force;self.force=False
            if keyframe:assert self._RTCRtpSender__encoder.last_keyframe_seconds is None
            if self.encode_seconds:await asyncio.sleep(self.encode_seconds)
            if index==self.fail_on:raise RuntimeError('Synthetic encoder failed')
            self._RTCRtpSender__encoder.applied_profile=profile
            return SimpleNamespace(index=index,keyframe=keyframe,profile=profile)
        finally:self.active-=1
    async def stop(self):self.track.stop()


async def settle(pipeline):
    # Let its one pending preparation finish, without requesting another.
    if pipeline.pending is not None:await asyncio.shield(pipeline.pending)


async def throughput(pipelined):
    sender=SenderDouble(.008,paced=True);pipeline=install_encoded_pipeline(sender) if pipelined else None
    times=[]
    try:
        for _ in range(45):
            await sender._next_encoded_frame(None);times.append(time.perf_counter())
            await asyncio.sleep(.014) # paced RTP transmission of this frame
        return (len(times)-1)/(times[-1]-times[0]),sender.maximum_active
    finally:await sender.stop()


async def main():
    serial,_=await throughput(False);overlapped,maximum=await throughput(True)
    assert overlapped>serial*1.2 and overlapped>50,(serial,overlapped)
    assert maximum==1,'The same codec must never encode two frames concurrently'

    sender=SenderDouble();pipeline=install_encoded_pipeline(sender);assert install_encoded_pipeline(sender) is pipeline
    await sender._next_encoded_frame(None);await settle(pipeline)
    assert sender.calls==2
    await asyncio.sleep(.03)
    assert sender.calls==2 and pipeline.stats['maximumPreparing']==1,'A blocked send may retain one encoded frame, never keep producing'
    sender.track.stop();await pipeline.wait_closed();assert not pipeline.tasks

    clock=[0.];sender=SenderDouble();encoder=sender._RTCRtpSender__encoder
    pipeline=install_encoded_pipeline(sender,clock=lambda:clock[0])
    await sender._next_encoded_frame(None);await settle(pipeline);clock[0]=.2
    aged=await sender._next_encoded_frame(None)
    assert aged.index==2 and not aged.keyframe and pipeline.stats['agedReturned']==1
    for index in range(3,13):
        await settle(pipeline);clock[0]+=.2 # repeated200ms frame transmissions
        aged=await sender._next_encoded_frame(None)
        assert aged.index==index and not aged.keyframe
    assert pipeline.stats['staleDiscarded']==pipeline.stats['keyframeRefreshes']==0,'A slow IDR must never trigger an automatic age→IDR feedback loop'
    await settle(pipeline);pipeline.invalidate()
    fresh=await sender._next_encoded_frame(None)
    assert fresh.keyframe and pipeline.stats['invalidatedDiscarded']==1,'Explicit playback resume must replace its old pending frame with a fresh IDR'
    assert sender._RTCRtpSender__encoder is encoder,'Playback recovery must not recreate the encoder'
    await settle(pipeline);sender.track.width=1280
    changed=await sender._next_encoded_frame(None)
    assert changed.keyframe and changed.profile[0]==1280 and pipeline.stats['profileDiscarded']==1,'An old-profile P-frame must be replaced by a new-profile IDR'
    await sender.stop();assert pipeline.closed and not pipeline.tasks

    sender=SenderDouble();sender.fail_on=2;pipeline=install_encoded_pipeline(sender)
    await sender._next_encoded_frame(None);await asyncio.sleep(0)
    try:await sender._next_encoded_frame(None)
    except RuntimeError as error:assert 'encoder failed' in str(error)
    else:raise AssertionError('Prefetched encoder exceptions must reach the RTP sender')
    await sender.stop();assert sender.calls==2 and not pipeline.tasks,(sender.calls,[(task.done(),task.cancelled()) for task in pipeline.tasks])

    sender=SenderDouble();sender.block=asyncio.Event();pipeline=install_encoded_pipeline(sender)
    waiting=asyncio.create_task(sender._next_encoded_frame(None));await asyncio.sleep(.01)
    await sender.stop()
    result=await asyncio.gather(waiting,return_exceptions=True)
    assert isinstance(result[0],asyncio.CancelledError) and sender.active==0 and not pipeline.tasks

    sender=SenderDouble();pipeline=install_encoded_pipeline(sender)
    await sender._next_encoded_frame(None);sender.track.streaming.clear()
    await asyncio.sleep(.03)
    assert sender.calls==1,'Paused capture must not create a runaway encoded-frame producer'
    await sender.stop();assert not pipeline.tasks

    # Exercise the pinned aiortc instance method and actual software H264
    # encoder, without creating an ICE offer, sockets or desktop capture.
    class RealTrack(VideoStreamTrack):
        def __init__(self):
            super().__init__();self.count=0;self.width=64;self.height=48;self.fps=60;self.fixed_profile=True
        async def recv(self):
            self.count+=1;value=av.VideoFrame(64,48,'yuv420p')
            for plane in value.planes:plane.update(bytes([128])*plane.buffer_size)
            value.pts=self.count*1500;value.time_base=fractions.Fraction(1,90000);value.opaque={'fps':60,'fixedProfile':True}
            return value
    pc=RTCPeerConnection(RTCConfiguration(iceServers=[]));track=RealTrack();actual=pc.addTrack(track);pipeline=install_encoded_pipeline(actual)
    codec=RTCRtpCodecParameters(mimeType='video/H264',clockRate=90000,payloadType=102)
    try:
        encoded=await actual._next_encoded_frame(codec)
        assert encoded.payloads and all(isinstance(payload,bytes) for payload in encoded.payloads)
        await settle(pipeline)
        assert track.count==2 and pipeline.stats['maximumPreparing']==1
    finally:track.stop();await actual.stop();await pc.close()
    assert not pipeline.tasks

    print(json.dumps({'serialEncodeSendFps':round(serial,2),'overlappedEncodeSendFps':round(overlapped,2),'maximumConcurrentEncodes':maximum,'maximumEncodedFramesAhead':1,'slow200msSendsDoNotTriggerIdrLoop':True,'explicitResumeRequiresFreshIdr':True,'profileChangeRequiresIdr':True,'encoderIdentityPreserved':True,'errorsAndCancellationVerified':True,'pausedProducerBounded':True,'realAiortcAndH264Verified':True,'desktopCaptured':False,'networkTraffic':False}))


with WindowsTimerResolution():run_realtime(main())
