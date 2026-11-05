import os
os.environ['OPENBLAS_NUM_THREADS']='1'
os.environ['OMP_NUM_THREADS']='1'
import asyncio, concurrent.futures, fractions, json, time, sys, ctypes, getpass, hashlib
from pathlib import Path
import av, dxcam, aiohttp
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer, RTCRtpSender, VideoStreamTrack
from aiortc.codecs.h264 import H264Encoder
import aiortc.codecs, aiortc.rtcrtpsender
from remote_input import RemoteInput
from desktop_audio import DesktopAudioTrack, MicrophoneSink

ROOT=Path(__file__).resolve().parent.parent
ctypes.windll.user32.SetProcessDPIAware()

class DeliveryPolicy:
    def __init__(self,max_fps):self.max_fps=max_fps;self.fps=max_fps;self.bandwidth=14000000;self.frame_bytes=0;self.updated=0
    def observe(self,size):
        self.frame_bytes=size if not self.frame_bytes else self.frame_bytes*.96+size*.04
        now=time.monotonic()
        if now-self.updated>1 and self.frame_bytes>0:
            sustainable=max(5,min(self.max_fps,int(self.bandwidth*.8/(8*self.frame_bytes))))
            self.fps=min(sustainable,self.fps+5);self.updated=now

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
                    codec.framerate=fractions.Fraction(int(target_fps));codec.time_base=fractions.Fraction(1,90000);codec.gop_size=int(target_fps);codec.max_b_frames=0
                    if encoder=='libx264':codec.profile='Baseline'
                    level='6.0' if frame.width*frame.height*target_fps>3840*2160*60 else ('5.2' if frame.width*frame.height>1920*1080 else '4.2')
                    codec.options=({'usage':'ultralowlatency','quality':'speed','rc':'cqp','qp_i':'16','qp_p':'16','profile':'constrained_baseline'} if encoder=='h264_amf' else {'preset':'ultrafast','tune':'zerolatency','level':level,'crf':'16','x264-params':'bframes=0:rc-lookahead=0:sync-lookahead=0:scenecut=0'})
                    codec.thread_count=4;codec.open();self.codec=codec;self.created=time.monotonic()
                    print(json.dumps({'encoder':encoder,'targetFps':60}),flush=True);break
                except Exception as error:
                    print(json.dumps({'encoderFallback':encoder,'reason':str(error)}),flush=True)
            if self.codec is None:raise RuntimeError('No H264 encoder is available.')
        started=time.perf_counter();packets=self.codec.encode(frame)
        self.encode_total=getattr(self,'encode_total',0)+(time.perf_counter()-started);self.encode_count=getattr(self,'encode_count',0)+1
        if self.encode_count%300==0:print(json.dumps({'encodeMs':round(1000*self.encode_total/self.encode_count,2)}),flush=True)
        data=b''.join(bytes(packet) for packet in packets)
        if self.delivery and not any(packet.is_keyframe for packet in packets):self.delivery.observe(len(data))
        if data:yield from self._split_bitstream(data)

original_encoder=aiortc.codecs.get_encoder
def encoder_factory(codec):
    return FastH264Encoder() if codec.mimeType.lower()=='video/h264' else original_encoder(codec)
aiortc.codecs.get_encoder=encoder_factory
aiortc.rtcrtpsender.get_encoder=encoder_factory

class DesktopTrack(VideoStreamTrack):
    def __init__(self,camera,width,height,fps):super().__init__();self.camera=camera;self.width=width;self.height=height;self.fps=fps;self.delivery=DeliveryPolicy(fps);self.last_delivery=0;self.first=None;self.sent=0;self.capture_pool=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='FrameRead')
    def read_frame(self):
        started=time.perf_counter();image=self.camera.get_latest_frame();ready=time.perf_counter()
        if image is None:raise RuntimeError('Desktop capture stopped.')
        now=time.perf_counter();self.first=self.first or now
        frame=av.VideoFrame.from_numpy_buffer(image,format='bgra')
        frame=frame.reformat(width=self.width,height=self.height,format='yuv420p',threads=2,interpolation='BILINEAR')
        frame.opaque={'fps':self.fps,'delivery':self.delivery}
        frame.pts=round((now-self.first)*90000);frame.time_base=fractions.Fraction(1,90000);self.sent+=1
        self.read_total=getattr(self,'read_total',0)+(ready-started);self.convert_total=getattr(self,'convert_total',0)+(time.perf_counter()-ready)
        if self.sent%300==0:print(json.dumps({'sentFrames':self.sent,'sourceFps':round(self.sent/max(.01,now-self.first),1),'captureWaitMs':round(1000*self.read_total/self.sent,2),'convertMs':round(1000*self.convert_total/self.sent,2)}),flush=True)
        return frame
    async def recv(self):
        pause=self.last_delivery+1/self.delivery.fps-time.perf_counter()
        if pause>0:await asyncio.sleep(pause)
        frame=await asyncio.get_running_loop().run_in_executor(self.capture_pool,self.read_frame)
        self.last_delivery=time.perf_counter();return frame
    def stop(self):super().stop();self.capture_pool.shutdown(wait=False,cancel_futures=True)

class DesktopSession:
    def __init__(self,owner):
        self.owner=owner;self.camera=owner.camera;self.input=owner.input
        self.source_width=owner.source_width;self.source_height=owner.source_height;self.refresh=owner.refresh
        self.pc=None;self.track=None;self.audio_track=None;self.mic_sink=None;self.mic_task=None;self.closed=False;self.audio_error=None;self.move_sequence=0
    async def answer(self,offer):
        preferences=offer.get('preferences',{})
        fps=max(15,min(120,self.refresh,int(preferences.get('fps',60))))
        width=min(self.source_width,{'720':1280,'1080':1920,'2160':3840}.get(str(preferences.get('quality')),self.source_width))
        height=max(2,round(self.source_height*width/self.source_width)//2*2);width=width//2*2
        if not self.camera.is_capturing:self.camera.start(target_fps=min(120,self.refresh),video_mode=True)
        self.pc=pc=RTCPeerConnection(RTCConfiguration(iceServers=[RTCIceServer(urls='stun:stun.cloudflare.com:3478')]))
        self.track=track=DesktopTrack(self.camera,width,height,fps);pc.addTrack(track)
        if 'm=audio' in offer.get('sdp',''):
            try:self.audio_track=DesktopAudioTrack();pc.addTrack(self.audio_track);self.audio_error=None
            except Exception as error:self.audio_error=str(error);print(json.dumps({'desktopAudioError':str(error)}),flush=True)
        self.mic_sink=MicrophoneSink()
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
                    if event.get('type')=='ping':
                        channel.send(json.dumps({'type':'pong','time':event.get('time'),'sent':track.sent,'fps':round(track.sent/max(.01,time.perf_counter()-(track.first or time.perf_counter())),1),'inputError':self.input.error,'source':{'width':self.source_width,'height':self.source_height,'refresh':self.refresh},'stream':{'width':width,'height':height,'fps':fps},'audio':self.audio_track is not None,'audioError':self.audio_error,'microphoneReady':bool(self.mic_sink and self.mic_sink.device)}))
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
            print(json.dumps({'connection':pc.connectionState}),flush=True)
            self.owner.update_input_state()
            if pc.connectionState in ['failed','closed']:asyncio.create_task(self.close())
        async def expire_unconnected():
            await asyncio.sleep(35)
            if not self.closed and pc.connectionState!='connected':await self.close()
        asyncio.create_task(expire_unconnected())
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer['sdp'],type=offer['type']))
        await pc.setLocalDescription(await pc.createAnswer())
        return {'sdp':pc.localDescription.sdp,'type':pc.localDescription.type}
    async def close(self):
        if self.closed:return
        self.closed=True
        def debug(stage):
            if os.environ.get('DESKTOP_RTC_DEBUG'):print(json.dumps({'disconnectStage':stage}),flush=True)
        debug('start')
        if self.mic_task:self.mic_task.cancel();self.mic_task=None
        if self.mic_sink:self.mic_sink.close();self.mic_sink=None
        if self.audio_track:self.audio_track.stop();self.audio_track=None
        debug('audio-stopped')
        if self.track:self.track.stop();self.track=None
        debug('track-stopped')
        if self.pc:pc=self.pc;self.pc=None;await pc.close()
        debug('peer-closed')
        if self in self.owner.sessions:self.owner.sessions.remove(self)
        self.owner.update_input_state()
        if not self.owner.sessions and self.camera.is_capturing:self.camera.stop()
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
    def update_input_state(self):
        if any(session.pc and session.pc.connectionState=='connected' for session in self.sessions):self.input.enabled=True
        else:self.input.disable()
    async def answer(self,offer):
        for session in list(self.sessions):
            if session.pc and session.pc.connectionState in ['closed','failed']:await session.close()
        if len(self.sessions)>=4:raise RuntimeError('Too many active desktop viewers. Close an unused viewer and retry.')
        session=DesktopSession(self);self.sessions.append(session)
        try:return await session.answer(offer)
        except BaseException:
            await session.close();raise
    async def close(self):
        self.closed=True
        for session in list(self.sessions):await session.close()
        self.input.disable();self.camera.stop();self.camera.release()

async def main():
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
                            answer=await asyncio.wait_for(agent.answer(pending['offer']),20)
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
