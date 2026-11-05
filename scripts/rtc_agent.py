import os
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
import asyncio, concurrent.futures, fractions, json, time, sys, ctypes, getpass, hashlib, uuid, math
import threading
from collections import deque,OrderedDict
from pathlib import Path
import av, dxcam, aiohttp
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer, RTCRtpSender, VideoStreamTrack, MediaStreamTrack, sdp
from aiortc.codecs.h264 import H264Encoder
import aiortc.codecs, aiortc.rtcrtpsender
from remote_input import RemoteInput
from desktop_audio import DesktopAudioTrack, MicrophoneSink, TimelineOpusEncoder
from low_latency_rtp import configure_video_playout_delay
from rtp_scheduling import install_cooperative_rtp_send

ROOT=Path(__file__).resolve().parent.parent
ctypes.windll.user32.SetProcessDPIAware()

class WindowsTimerResolution:
    def __init__(self,milliseconds=1,winmm=None):self.milliseconds=milliseconds;self.winmm=winmm;self.active=False
    def __enter__(self):
        try:
            self.winmm=self.winmm or ctypes.WinDLL('winmm')
            self.active=self.winmm.timeBeginPeriod(self.milliseconds)==0
        except (OSError,AttributeError):self.active=False
        print(json.dumps({'timerResolutionMs':self.milliseconds if self.active else None}),flush=True)
        return self
    def __exit__(self,exc_type,exc_value,traceback):
        if self.active:
            self.active=False
            try:self.winmm.timeEndPeriod(self.milliseconds)
            except (OSError,AttributeError):pass
        return False

def independent_media_sender(sender,identity):
    # aiortc 1.15 exposes send(parameters), but has no setStreams API.
    # Keep the override scoped to this sender and match the advertised SDP.
    sender._stream_id=identity
    original_send=sender.send
    async def send(parameters):
        parameters.rtcp.cname=identity
        await original_send(parameters)
    sender.send=send

def independent_media_description(description,cnames):
    parsed=sdp.SessionDescription.parse(description.sdp)
    for media in parsed.media:
        if media.kind in cnames:
            for source in media.ssrc:source.cname=cnames[media.kind]
    return RTCSessionDescription(sdp=str(parsed),type=description.type)

class DeliveryPolicy:
    def __init__(self,max_fps):
        self.max_fps=max_fps;self.fps=max_fps;self.bandwidth=14000000;self.frame_bytes=0;self.updated=0
        self.last_feedback=None;self.last_buffer=None;self.congestion_streak=0;self.reason='target-no-confirmed-congestion'
    def observe(self,size,now=None):
        self.frame_bytes=size if not self.frame_bytes else self.frame_bytes*.96+size*.04
        now=time.monotonic() if now is None else now
        # REMB may measure a mostly static desktop's offered traffic rather
        # than path capacity. Never turn that estimate alone into a hard cap.
        if self.fps<self.max_fps and now-self.updated>=5 and (self.last_feedback is None or now-self.last_feedback>=5):
            self.fps=min(self.max_fps,self.fps+5);self.updated=now;self.reason='probe-without-recent-congestion-feedback'
    def receiver_feedback(self,report,now=None):
        now=time.monotonic() if now is None else now
        buffer=report.get('bufferMs');loss=report.get('lossDelta')
        number=lambda value:isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
        if not number(buffer) or buffer<0:return
        lost=number(loss) and loss>0
        growing=self.last_buffer is not None and buffer-self.last_buffer>=15
        congested=buffer>=250 or (lost and (growing or buffer>=80))
        self.last_feedback=now;self.last_buffer=buffer
        self.congestion_streak=self.congestion_streak+1 if congested else 0
        if self.congestion_streak>=2 and now-self.updated>=4:
            floor=min(self.max_fps,20)
            budget=max(floor,min(self.max_fps,int(self.bandwidth*.8/(8*max(1,self.frame_bytes)))))
            gentle=max(floor,int(self.fps*.8))
            self.fps=max(gentle,min(self.fps-1,budget));self.updated=now
            self.reason='confirmed-loss-or-persistent-buffer'
        elif not lost and buffer<=80:
            self.fps=self.max_fps;self.updated=now;self.reason='healthy-receiver-target'
        elif not congested and self.fps<self.max_fps and now-self.updated>=5:
            self.fps=min(self.max_fps,self.fps+5);self.updated=now;self.reason='recovering-receiver-probe'

class FastH264Encoder(H264Encoder):
    @property
    def target_bitrate(self):return getattr(self,'requested_bitrate',14000000)
    @target_bitrate.setter
    def target_bitrate(self,value):
        self.requested_bitrate=max(350000,min(int(value),100000000))
        if getattr(self,'delivery',None):self.delivery.bandwidth=self.requested_bitrate
    def _encode_frame(self, frame, force_keyframe):
        metadata=frame.opaque if isinstance(frame.opaque,dict) else {'fps':frame.opaque or 60}
        self.delivery=metadata.get('delivery')
        if self.delivery:self.delivery.bandwidth=self.target_bitrate
        if self.codec and (self.codec.width!=frame.width or self.codec.height!=frame.height):self.codec=None
        frame.pict_type=av.video.frame.PictureType.I if force_keyframe else av.video.frame.PictureType.NONE
        if self.codec is None:
            for encoder in ['libx264']:
                try:
                    codec=av.CodecContext.create(encoder,'w');codec.width=frame.width;codec.height=frame.height;codec.pix_fmt='yuv420p'
                    target_fps=metadata['fps']
                    codec.framerate=fractions.Fraction(int(target_fps));codec.time_base=fractions.Fraction(1,90000);codec.gop_size=120;codec.max_b_frames=0
                    if encoder=='libx264':codec.profile='Baseline'
                    level='6.0' if frame.width*frame.height*target_fps>3840*2160*60 else ('5.2' if frame.width*frame.height>1920*1080 else '4.2')
                    codec.options=({'usage':'ultralowlatency','quality':'speed','rc':'cqp','qp_i':'16','qp_p':'16','profile':'constrained_baseline'} if encoder=='h264_amf' else {'preset':'ultrafast','tune':'zerolatency','level':level,'crf':'16','x264-params':'bframes=0:rc-lookahead=0:sync-lookahead=0:scenecut=0'})
                    codec.thread_count=4;codec.open();self.codec=codec;self.created=time.monotonic()
                    print(json.dumps({'encoder':encoder,'targetFps':target_fps,'sessionId':metadata.get('sessionId')}),flush=True);break
                except Exception as error:
                    print(json.dumps({'encoderFallback':encoder,'reason':str(error)}),flush=True)
            if self.codec is None:raise RuntimeError('No H264 encoder is available.')
        started=time.perf_counter();packets=self.codec.encode(frame)
        self.encode_total=getattr(self,'encode_total',0)+(time.perf_counter()-started);self.encode_count=getattr(self,'encode_count',0)+1
        if self.encode_count%300==0:print(json.dumps({'encodeMs':round(1000*self.encode_total/self.encode_count,2),'sessionId':metadata.get('sessionId')}),flush=True)
        data=b''.join(bytes(packet) for packet in packets)
        if self.delivery and not any(packet.is_keyframe for packet in packets):self.delivery.observe(len(data))
        if data:yield from self._split_bitstream(data)

original_encoder=aiortc.codecs.get_encoder
def encoder_factory(codec):
    kind=codec.mimeType.lower()
    if kind=='video/h264':return FastH264Encoder()
    if kind=='audio/opus':return TimelineOpusEncoder()
    return original_encoder(codec)
aiortc.codecs.get_encoder=encoder_factory
aiortc.rtcrtpsender.get_encoder=encoder_factory

class SharedFrameSource:
    """One latest immutable raw/converted snapshot per source refresh tick."""
    def __init__(self,camera,refresh=60,clock=time.perf_counter):
        self.camera=camera;self.refresh=max(1,refresh);self.clock=clock;self.lock=threading.Lock();self.closed=False
        self.raw=None;self.tick=None;self.captured_at=None;self.converted=OrderedDict();self.reformatter=av.video.reformatter.VideoReformatter()
        self.grabs=0;self.conversions=0
    def get_yuv(self,width,height):
        started=self.clock()
        with self.lock:
            if self.closed:raise RuntimeError('Desktop frame source is closed.')
            if not self.camera.is_capturing:raise RuntimeError('Desktop capture stopped.')
            tick=int(self.clock()*self.refresh)
            if self.raw is None or tick!=self.tick:
                image=self.camera.grab()
                if image is None and self.camera.is_capturing:image=self.camera.get_latest_frame()
                if image is None:raise RuntimeError('Desktop capture stopped.')
                self.raw=image;self.tick=tick;self.captured_at=self.clock();self.grabs+=1;self.converted.clear()
            ready=self.clock();key=(width,height)
            if key in self.converted:
                pixels=self.converted[key];self.converted.move_to_end(key)
            else:
                frame=av.VideoFrame.from_numpy_buffer(self.raw,format='bgra')
                frame=self.reformatter.reformat(frame,width=width,height=height,format='yuv420p',threads=4,interpolation='BILINEAR')
                # Each replacement owns new storage. Existing VideoFrame
                # wrappers keep their old array alive after cache eviction.
                pixels=frame.to_ndarray();pixels.setflags(write=False)
                self.converted[key]=pixels;self.conversions+=1
                while len(self.converted)>4:self.converted.popitem(last=False)
            return pixels,self.captured_at,ready-started,self.clock()-ready
    def close(self):
        with self.lock:
            self.closed=True;self.raw=None;self.converted.clear()
    def stop_capture(self):
        # Prevent a concurrent grab() from falling through to DXGI's one-shot
        # path after capture stops. Frame workers only read the ring buffer.
        with self.lock:
            self.camera.stop();self.raw=None;self.tick=None;self.converted.clear()

class DesktopTrack(VideoStreamTrack):
    def __init__(self,camera,width,height,fps,session_id=None,frame_source=None):super().__init__();self.camera=camera;self.width=width;self.height=height;self.fps=fps;self.session_id=session_id;self.delivery=DeliveryPolicy(fps);self.last_delivery=0;self.first=None;self.sent=0;self.frame_samples=deque(maxlen=1024);self.frame_source=frame_source or SharedFrameSource(camera,max(60,fps));self.owns_frame_source=frame_source is None;self.capture_pool=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='FrameRead')
    def delivery_diagnostics(self,now=None):
        now=time.perf_counter() if now is None else now
        samples=[sample for sample in list(self.frame_samples) if sample[0]>=now-5]
        actual=(samples[-1][1]-samples[0][1])/(samples[-1][0]-samples[0][0]) if len(samples)>1 and samples[-1][0]>samples[0][0] else 0
        return {'deliveryFps':self.delivery.fps,'estimatedMbps':round(self.delivery.bandwidth/1e6,3),'avgFrameKB':round(self.delivery.frame_bytes/1024,2),'actualFps':round(actual,1),'deliveryTargetFps':self.delivery.max_fps,'deliveryReason':self.delivery.reason}
    def read_frame(self):
        pixels,captured_at,read_time,convert_time=self.frame_source.get_yuv(self.width,self.height)
        now=time.perf_counter();self.first=self.first or now
        # Never share AVFrame metadata/pict_type across encoder threads.
        frame=av.VideoFrame.from_numpy_buffer(pixels,format='yuv420p')
        frame.opaque={'fps':self.fps,'delivery':self.delivery,'sessionId':self.session_id}
        frame.pts=round((now-self.first)*90000);frame.time_base=fractions.Fraction(1,90000);self.sent+=1
        self.frame_samples.append((now,self.sent))
        self.read_total=getattr(self,'read_total',0)+read_time;self.convert_total=getattr(self,'convert_total',0)+convert_time
        if self.sent%300==0:print(json.dumps({'sessionId':self.session_id,'width':self.width,'height':self.height,'targetFps':self.fps,'sentFrames':self.sent,'sourceFps':round(self.sent/max(.01,now-self.first),1),'captureWaitMs':round(1000*self.read_total/self.sent,2),'convertMs':round(1000*self.convert_total/self.sent,2),'sharedGrabs':self.frame_source.grabs,'sharedConversions':self.frame_source.conversions,**self.delivery_diagnostics(now)}),flush=True)
        return frame
    async def recv(self):
        interval=1/self.delivery.fps;deadline=self.last_delivery+interval;now=time.perf_counter()
        # Pace frame starts. Conversion and encoding consume this frame's
        # budget instead of being added after a full frame interval.
        if now-deadline>=interval:deadline=now
        pause=deadline-now
        if pause>0:await asyncio.sleep(pause)
        self.last_delivery=deadline
        frame=await asyncio.get_running_loop().run_in_executor(self.capture_pool,self.read_frame)
        return frame
    def stop(self):
        super().stop();self.capture_pool.shutdown(wait=False,cancel_futures=True)
        if self.owns_frame_source:self.frame_source.close()

class MediaDevices:
    """Serialize blocking PortAudio lifecycle away from the media event loop."""
    def __init__(self):
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='MediaDevices')
        self.cleanup_tasks=set();self.open_tasks=set();self.closed=False
    async def call(self,function,*args):
        return await asyncio.shield(asyncio.get_running_loop().run_in_executor(self.pool,function,*args))
    async def open(self,factory,close_method):
        if self.closed:raise RuntimeError('Media devices are closed.')
        completion=asyncio.get_running_loop().create_future();self.open_tasks.add(completion)
        pending=asyncio.get_running_loop().run_in_executor(self.pool,factory)
        disposed=False
        try:
            resource=await asyncio.shield(pending)
            if self.closed:
                disposed=True
                await self.call(getattr(resource,close_method))
                raise RuntimeError('Media devices closed during setup.')
            return resource
        except BaseException:
            # Cancelling run_in_executor cannot stop its running constructor.
            # Retain ownership until a late resource has been closed.
            async def reclaim():
                try:
                    resource=await asyncio.shield(pending)
                    if not disposed:await self.call(getattr(resource,close_method))
                except Exception as error:print(json.dumps({'mediaCleanupError':str(error)}),flush=True)
            cleanup=asyncio.create_task(reclaim());self.cleanup_tasks.add(cleanup);cleanup.add_done_callback(self.cleanup_tasks.discard)
            raise
        finally:
            self.open_tasks.discard(completion)
            if not completion.done():completion.set_result(None)
    def nonblocking_track_stop(self,track):
        original_stop=track.stop;pending=None
        def stop():
            nonlocal pending
            # aiortc calls stop() directly when its RTP task ends. Emit the
            # public track event here; PortAudio teardown stays on its worker.
            MediaStreamTrack.stop(track)
            if pending is None:pending=self.pool.submit(original_stop)
            return pending
        track.stop=stop
    async def close_track(self,track):
        pending=track.stop()
        if isinstance(pending,concurrent.futures.Future):await asyncio.shield(asyncio.wrap_future(pending))
    async def close(self):
        self.closed=True
        pending=list(self.open_tasks)
        if pending:await asyncio.gather(*pending,return_exceptions=True)
        while self.cleanup_tasks:await asyncio.gather(*list(self.cleanup_tasks),return_exceptions=True)
        await asyncio.to_thread(self.pool.shutdown,wait=True)

class DesktopSession:
    def __init__(self,owner,session_id=None):
        self.owner=owner;self.camera=owner.camera;self.input=owner.input
        self.source_width=owner.source_width;self.source_height=owner.source_height;self.refresh=owner.refresh
        self.pc=None;self.track=None;self.audio_track=None;self.mic_sink=None;self.mic_task=None;self.closed=False;self.audio_error=None;self.move_sequence=0
        self.session_id=session_id or str(uuid.uuid4());self.created_at=time.monotonic();self.last_activity=self.created_at;self.watchdog=None;self.answer_result=None;self.close_task=None
        self.fps_samples=deque();self.last_report_log=0
    def recent_fps(self,track,now=None):
        now=time.perf_counter() if now is None else now
        self.fps_samples.append((now,track.sent))
        while len(self.fps_samples)>1 and self.fps_samples[0][0]<now-5:self.fps_samples.popleft()
        started,count=self.fps_samples[0];elapsed=now-started
        return round((track.sent-count)/elapsed,1) if elapsed>0 else 0
    def report_receiver(self,event,now=None):
        now=time.monotonic() if now is None else now
        report=event.get('receiverReport')
        if not isinstance(report,dict):return
        fields=['fps','bufferMs','lossDelta','decodeMs','mbps','inputAckMs','inputAckAgeMs','audioBufferMs','audioTotalSamplesDuration','audioTotalSamplesReceived','audioConcealedSamplesDelta','audioTotalPlayoutDelay','audioTotalSamplesCount','audioPlayoutMs','audioPlayoutSamplesDuration','audioBaseLatencyMs','audioOutputLatencyMs','videoDecodedDelta','videoDroppedDelta','playbackFramesDelta','playbackDroppedDelta']
        values={key:value for key in fields if isinstance(value:=report.get(key),(int,float)) and not isinstance(value,bool) and math.isfinite(value)}
        if getattr(self,'track',None):self.track.delivery.receiver_feedback(values,now=now)
        if now-self.last_report_log<5:return
        if values:
            self.last_report_log=now
            diagnostics=self.track.delivery_diagnostics() if getattr(self,'track',None) else {}
            print(json.dumps({'sessionId':self.session_id,'receiverReport':values,**diagnostics}),flush=True)
    def expired(self,now=None):
        now=time.monotonic() if now is None else now
        if self.closed:return True
        state=self.pc.connectionState if self.pc else 'new'
        if state in ['closed','failed']:return True
        if state in ['connected','disconnected']:return now-self.last_activity>30
        return now-self.created_at>35
    async def answer(self,offer):
        preferences=offer.get('preferences',{})
        fps=max(15,min(120,self.refresh,int(preferences.get('fps',60))))
        width=min(self.source_width,{'720':1280,'1080':1920,'2160':3840}.get(str(preferences.get('quality')),self.source_width))
        height=max(2,round(self.source_height*width/self.source_width)//2*2);width=width//2*2
        if not self.camera.is_capturing:self.camera.start(target_fps=min(120,self.refresh),video_mode=True)
        self.pc=pc=RTCPeerConnection(RTCConfiguration(iceServers=[RTCIceServer(urls='stun:stun.cloudflare.com:3478')]))
        self.track=track=DesktopTrack(self.camera,width,height,fps,self.session_id,frame_source=self.owner.frame_source)
        cnames={'video':self.session_id+'-video','audio':self.session_id+'-audio'}
        independent_media_sender(pc.addTrack(track),cnames['video'])
        print(json.dumps({'sessionId':self.session_id,'streamStarted':True,'width':width,'height':height,'targetFps':fps}),flush=True)
        if 'm=audio' in offer.get('sdp',''):
            try:
                audio=await self.owner.media_devices.open(DesktopAudioTrack,'stop')
                if self.closed:
                    await self.owner.media_devices.call(audio.stop)
                    raise RuntimeError('Desktop session closed during audio setup.')
                self.owner.media_devices.nonblocking_track_stop(audio);self.audio_track=audio
                independent_media_sender(pc.addTrack(audio),cnames['audio']);self.audio_error=None
            except Exception as error:self.audio_error=str(error);print(json.dumps({'desktopAudioError':str(error)}),flush=True)
        if self.closed:raise RuntimeError('Desktop session closed during media setup.')
        sink=await self.owner.media_devices.open(MicrophoneSink,'close')
        if self.closed:
            await self.owner.media_devices.call(sink.close)
            raise RuntimeError('Desktop session closed during microphone setup.')
        self.mic_sink=sink
        @pc.on('track')
        def on_remote_track(remote_track):
            if remote_track.kind=='audio':self.mic_task=asyncio.create_task(self.mic_sink.consume(remote_track))
        for transceiver in pc.getTransceivers():
            if transceiver.kind=='video':transceiver.setCodecPreferences([codec for codec in RTCRtpSender.getCapabilities('video').codecs if codec.mimeType.lower()=='video/h264'])
        @pc.on('datachannel')
        def on_channel(channel):
            loop=asyncio.get_running_loop()
            def input_result(identifier,error):
                def reply():
                    if channel.readyState=='open':channel.send(json.dumps({'type':'inputAck','id':identifier,'error':error}))
                loop.call_soon_threadsafe(reply)
            @channel.on('message')
            def on_message(message):
                try:
                    event=json.loads(message)
                    self.last_activity=time.monotonic()
                    if event.get('type')=='ping':
                        self.report_receiver(event)
                        channel.send(json.dumps({'type':'pong','time':event.get('time'),'sent':track.sent,'fps':self.recent_fps(track),'inputError':self.input.error,'source':{'width':self.source_width,'height':self.source_height,'refresh':self.refresh},'stream':{'width':track.width,'height':track.height,'fps':track.fps},'audio':self.audio_track is not None,'audioError':self.audio_error,'microphoneReady':bool(self.mic_sink and self.mic_sink.device)}))
                    elif event.get('type')=='settings':
                        target_width=min(self.source_width,{'720':1280,'1080':1920,'2160':3840}.get(str(event.get('quality')),self.source_width))//2*2
                        track.width=target_width;track.height=max(2,round(self.source_height*target_width/self.source_width)//2*2)
                        track.fps=max(15,min(120,self.refresh,int(event.get('fps',60))));track.delivery=DeliveryPolicy(track.fps)
                    elif event.get('type')=='disconnect':asyncio.create_task(self.close())
                    else:
                        if event.get('type')=='move' and event.get('seq'):
                            if event['seq']<=self.move_sequence:return
                            self.move_sequence=event.pop('seq')
                        self.input.submit(event,input_result)
                except Exception:pass
            @channel.on('close')
            def on_close():self.input.release_all()
        @pc.on('connectionstatechange')
        async def on_state():
            print(json.dumps({'sessionId':self.session_id,'connection':pc.connectionState}),flush=True)
            if pc.connectionState=='connected':self.last_activity=time.monotonic()
            self.owner.update_input_state()
            if pc.connectionState in ['failed','closed']:asyncio.create_task(self.close())
        async def expire_idle():
            while not self.closed:
                await asyncio.sleep(5)
                if self.expired():
                    print(json.dumps({'sessionId':self.session_id,'expired':True,'connection':pc.connectionState}),flush=True)
                    await self.close()
        self.watchdog=asyncio.create_task(expire_idle())
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer['sdp'],type=offer['type']))
        for transceiver in pc.getTransceivers():install_cooperative_rtp_send(transceiver.sender.transport)
        playout=configure_video_playout_delay(pc,offer['sdp'])
        print(json.dumps({'sessionId':self.session_id,'videoPlayoutDelay':playout}),flush=True)
        await pc.setLocalDescription(independent_media_description(await pc.createAnswer(),cnames))
        return {'sdp':pc.localDescription.sdp,'type':pc.localDescription.type}
    async def close(self):
        if self.close_task is None:
            if self.closed:return
            self.close_task=asyncio.create_task(self._close())
        await asyncio.shield(self.close_task)
    async def _close(self):
        if self.closed:return
        self.closed=True
        if self.watchdog and self.watchdog is not asyncio.current_task():self.watchdog.cancel()
        self.watchdog=None;self.answer_result=None
        def debug(stage):
            if os.environ.get('DESKTOP_RTC_DEBUG'):print(json.dumps({'disconnectStage':stage}),flush=True)
        debug('start')
        if self.mic_task:
            task=self.mic_task;self.mic_task=None;task.cancel();await asyncio.gather(task,return_exceptions=True)
        if self.mic_sink:
            sink=self.mic_sink;self.mic_sink=None;await self.owner.media_devices.call(sink.close)
        if self.audio_track:
            audio=self.audio_track;self.audio_track=None;await self.owner.media_devices.close_track(audio)
        debug('audio-stopped')
        if self.track:self.track.stop();self.track=None
        debug('track-stopped')
        if self.pc:pc=self.pc;self.pc=None;await pc.close()
        debug('peer-closed')
        if self in self.owner.sessions:self.owner.sessions.remove(self)
        self.owner.update_input_state()
        if not self.owner.sessions and self.camera.is_capturing:self.owner.frame_source.stop_capture()
        debug('session-stopped')

class DesktopAgent:
    def __init__(self):
        self.camera=dxcam.create(output_color='BGRA',max_buffer_len=2,processor_backend='numpy')
        self.input=RemoteInput();self.sessions=[];self.closed=False
        self.source_width=ctypes.windll.user32.GetSystemMetrics(0);self.source_height=ctypes.windll.user32.GetSystemMetrics(1)
        ctypes.windll.user32.GetDC.restype=ctypes.c_void_p
        screen_dc=ctypes.windll.user32.GetDC(None)
        ctypes.windll.gdi32.GetDeviceCaps.argtypes=[ctypes.c_void_p,ctypes.c_int]
        self.refresh=max(30,min(240,ctypes.windll.gdi32.GetDeviceCaps(screen_dc,116) or 60))
        ctypes.windll.user32.ReleaseDC.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
        ctypes.windll.user32.ReleaseDC(None,screen_dc)
        self.frame_source=SharedFrameSource(self.camera,min(120,self.refresh))
        self.media_devices=MediaDevices()
    def update_input_state(self):
        if any(session.pc and session.pc.connectionState=='connected' for session in self.sessions):self.input.enabled=True
        else:self.input.disable()
    async def answer(self,offer,session_id=None):
        for session in list(self.sessions):
            if session.expired():await session.close()
        if session_id:
            for session in self.sessions:
                if session.session_id==session_id and session.answer_result is not None:return session.answer_result
        if len(self.sessions)>=4:raise RuntimeError('Too many active desktop viewers. Close an unused viewer and retry.')
        session=DesktopSession(self,session_id);self.sessions.append(session)
        try:
            session.answer_result=await session.answer(offer)
            return session.answer_result
        except BaseException:
            await session.close();raise
    async def close(self):
        self.closed=True
        for session in list(self.sessions):await session.close()
        await self.media_devices.close()
        self.input.disable();self.frame_source.close();self.camera.stop();self.camera.release()

async def main():
    with WindowsTimerResolution():
        await run_agent()

async def run_agent():
    agent=DesktopAgent()
    try:
        if '--local' in sys.argv:
            app=web.Application()
            async def page(request):
                content=(ROOT/'web'/'viewer.html').read_text(encoding='utf-8').replace('//__GESTURES__',(ROOT/'web'/'gestures.mjs').read_text(encoding='utf-8').replace('export class','class'))
                return web.Response(text=content,content_type='text/html')
            async def offer(request):return web.json_response(await agent.answer(await request.json()))
            app.router.add_get('/',page);app.router.add_post('/api/offer',offer)
            runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',18742);await site.start()
            print('Local: http://127.0.0.1:18742',flush=True);await asyncio.Event().wait()
        else:
            print('Ready for private connection configuration on stdin.',flush=True)
            line=await asyncio.to_thread(getpass.getpass,'Private connection configuration (hidden): ') if sys.stdin.isatty() else await asyncio.to_thread(sys.stdin.readline)
            if not line:raise RuntimeError('Supply the private connection configuration through an interactive terminal.')
            config=json.loads(line);url=config['url'];token=config.get('token');agent_key=config.get('agentKey');seen=None
            timeout=aiohttp.ClientTimeout(total=20)
            auth_headers={}
            if token:auth_headers['OAI-Sites-Authorization']='Bearer '+token
            if agent_key:auth_headers['X-Agent-Key']=agent_key
            async with aiohttp.ClientSession(headers=auth_headers,timeout=timeout,trust_env=True,cookie_jar=aiohttp.DummyCookieJar()) as session:
                while not agent.closed:
                    try:
                        async with session.get(url+'/api/agent/poll') as response:
                            if response.status!=200:raise RuntimeError('Private Site authorization: HTTP '+str(response.status))
                            pending=await response.json()
                        if not getattr(agent,'service_authenticated',False):
                            agent.service_authenticated=True;print(json.dumps({'serviceAuthenticated':True,'waitingForViewer':True}),flush=True)
                        if pending.get('id') and pending['id']!=seen and time.time()*1000-pending.get('createdAt',0)<60000:
                            answer=await asyncio.wait_for(agent.answer(pending['offer'],session_id=pending['id']),20)
                            async with session.post(url+'/api/answer/'+pending['id'],json=answer) as response:
                                if response.status!=200:raise RuntimeError('Could not publish WebRTC answer.')
                            print(json.dumps({'sessionReady':pending['id']}),flush=True)
                            seen=pending['id']
                        await asyncio.sleep(.4)
                    except Exception as error:
                        print(str(error),flush=True);await asyncio.sleep(2)
    finally:await agent.close()

if __name__=='__main__':
    if '--local' not in sys.argv:
        kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel32.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p];kernel32.CreateMutexW.restype=ctypes.c_void_p
        mutex_name='Local\\CodexDesktopAgent_'+hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:16]
        ctypes.set_last_error(0);instance_mutex=kernel32.CreateMutexW(None,False,mutex_name)
        if not instance_mutex:raise OSError(ctypes.get_last_error(),'Could not initialize the desktop-agent instance lock.')
        if ctypes.get_last_error()==183:
            print('This desktop agent is already running. Duplicate startup was prevented.',flush=True);sys.exit(0)
    try:asyncio.run(main())
    except KeyboardInterrupt:pass
