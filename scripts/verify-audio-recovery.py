"""Audio device failure / load tests with fake PortAudio, never real devices."""
import asyncio
import fractions
import threading
import time
import av
import numpy as np
import desktop_audio as audio
from audio_buffer import LatestAudioFrames,PcmPlaybackBuffer
from audio_routing import AudioRouter,MicrophoneRouting
import audio_routing as routing_module


def buffers():
    frames=LatestAudioFrames(3)
    for value in range(10000):frames.put(value)
    assert list(frames.frames)==[9997,9998,9999] and frames.dropped==9997
    frames.close();assert frames.get(.001) is None and not frames.put(1)
    pcm=PcmPlaybackBuffer(16,4);pcm.put(bytes(range(12)));pcm.put(bytes(range(12,24)))
    assert len(pcm.data)==16 and pcm.dropped_bytes==8
    assert pcm.read(2)==bytes(range(8,16))
    assert pcm.read(4)==bytes(range(16,24))+bytes(8) and pcm.underruns==1
    pcm.put(bytes(range(40)));assert pcm.read(4)==bytes(range(24,40))


class FakeStream:
    def __init__(self,callback):self.callback=callback;self.active=True;self.closes=0
    def is_active(self):return self.active
    def close(self):self.active=False;self.closes+=1


class FakeAudio:
    gate=threading.Event();output_attempts=0;streams=[];instances=[]
    def __init__(self):self.terminated=False;self.instances.append(self)
    def get_device_info_generator(self):
        return iter([{'name':'Hi-Fi CABLE Output','maxInputChannels':2,'maxOutputChannels':0,'hostApi':1,'index':0,'defaultSampleRate':48000},
                     {'name':'CABLE Input','maxInputChannels':0,'maxOutputChannels':2,'hostApi':1,'index':1,'defaultSampleRate':48000}])
    def get_host_api_info_by_type(self,kind):return {'index':1}
    def is_format_supported(self,*args,**kwargs):return True
    def open(self,**kwargs):
        if kwargs.get('output'):
            type(self).output_attempts+=1
            if self.output_attempts==1:
                self.gate.wait(1)
                raise OSError('Simulated endpoint restart')
        stream=FakeStream(kwargs['stream_callback']);self.streams.append(stream);return stream
    def terminate(self):self.terminated=True


async def wait_until(predicate,timeout=3):
    started=time.monotonic()
    while not predicate():
        assert time.monotonic()-started<timeout,'Audio worker did not recover'
        await asyncio.sleep(.005)


def frame(pts):
    result=av.AudioFrame.from_ndarray(np.ones((1,1920),dtype=np.int16),format='s16',layout='stereo')
    result.sample_rate=48000;result.pts=pts;result.time_base=fractions.Fraction(1,48000);return result


async def devices():
    original=audio.pyaudio.PyAudio;audio.pyaudio.PyAudio=FakeAudio
    try:
        capture=audio.DesktopAudioTrack();old=capture.stream
        old.callback(np.ones(480,dtype=np.int16).tobytes(),240,{},0)
        first=await capture.recv();assert first.samples==240
        capture.last_capture-=2
        await asyncio.to_thread(capture._recover_capture)
        assert capture.recoveries==1 and old.closes==1 and capture.stream is not old
        capture.stream.callback(np.ones(480,dtype=np.int16).tobytes(),240,{},0)
        resumed=await capture.recv();assert resumed.pts>first.pts
        await asyncio.to_thread(capture.stop);assert capture.readyState=='ended'

        class Source:
            count=0
            async def recv(self):
                if self.count>=200:await asyncio.Event().wait()
                await asyncio.sleep(0)
                value=frame(self.count*960);self.count+=1;return value
        source=Source();sink=audio.MicrophoneSink();consume=asyncio.create_task(sink.consume(source))
        # Opening the fake device is blocked; receiving must continue and
        # retain at most three frames rather than a whole network backlog.
        await wait_until(lambda:source.count==200)
        assert len(sink.frames.frames)<=3 and sink.frames.dropped>0
        FakeAudio.gate.set()
        await wait_until(lambda:sink.stream is not None)
        first_stream=sink.stream;first_stream.active=False
        await wait_until(lambda:sink.stream is not None and sink.stream is not first_stream)
        assert first_stream.closes==1 and sink.recoveries>=2
        sink.frames.put(frame(300*960))
        await wait_until(lambda:len(sink.playback.data)>0)
        data,status=sink.stream.callback(None,240,{},0)
        assert len(data)==960 and status==audio.pyaudio.paContinue
        consume.cancel();await asyncio.gather(consume,return_exceptions=True)
        await asyncio.to_thread(sink.close)
        assert not sink.worker.is_alive() and all(instance.terminated for instance in FakeAudio.instances)
    finally:audio.pyaudio.PyAudio=original;FakeAudio.gate.set()


def routing():
    class Core:
        def __init__(self):self.defaults={role:'physical' for role in range(3)};self.fail=True
        def default(self,flow,role):return self.defaults[role]
        def set_default(self,identifier,role):
            if role==1 and identifier=='cable' and self.fail:self.fail=False;raise OSError('Endpoint busy')
            self.defaults[role]=identifier
    core=Core();routing=MicrophoneRouting();capture=[('Microphone','physical'),('CABLE Output','cable')]
    try:routing.apply(core,capture,'cable',True)
    except OSError:pass
    assert core.defaults[0]=='cable' and core.defaults[1]=='physical'
    routing.apply(core,capture,'cable',True);assert set(core.defaults.values())=={'cable'}
    # Respect a manual change when releasing the remote microphone.
    core.defaults[2]='user-selected'
    routing.apply(core,capture,'cable',False)
    assert core.defaults=={0:'physical',1:'physical',2:'user-selected'}

    # A single hot-unplug enumeration error must not terminate the router.
    router=AudioRouter();calls=[];cores=[]
    class RouterCore:
        closed=False
        def close(self):self.closed=True
    def new_core():
        result=RouterCore();cores.append(result);return result
    class Wake:
        passes=0
        def wait(self,timeout):
            self.passes+=1
            if self.passes==2:router.stop_event.set()
        def clear(self):pass
    def endpoints(flow):
        calls.append(flow)
        if len(calls)==1:raise OSError('Simulated removed endpoint')
        return []
    old_core,old_endpoints=routing_module.CoreAudio,routing_module.endpoints
    routing_module.CoreAudio=new_core;routing_module.endpoints=endpoints;router.wake_event=Wake()
    try:router.run()
    finally:routing_module.CoreAudio=old_core;routing_module.endpoints=old_endpoints
    assert len(calls)==3 and router.error is None and len(cores)==2 and all(core.closed for core in cores)

    # A permanently disconnected proxy must be replaced, and a partial
    # microphone switch must still restore the original role values.
    defaults={role:'original-'+str(role) for role in range(3)};created=[]
    class RestartedCore:
        def __init__(self):self.generation=len(created);self.closed=False;created.append(self)
        def default(self,flow,role):return defaults[role]
        def set_default(self,identifier,role):
            if self.generation==0 and role==1:raise OSError('RPC_E_DISCONNECTED')
            defaults[role]=identifier
        def close(self):self.closed=True
    router=AudioRouter();router.set_microphone('phone',True);router.wake_event=Wake()
    routing_module.CoreAudio=RestartedCore;routing_module.endpoints=lambda flow:capture if flow==1 else []
    try:router.run()
    finally:routing_module.CoreAudio=old_core;routing_module.endpoints=old_endpoints
    assert len(created)==2 and all(core.closed for core in created) and router.error is None
    assert defaults=={role:'original-'+str(role) for role in range(3)},'Original defaults must survive a COM proxy replacement'


async def main():
    buffers();routing();await devices()
    print('Audio queue caps, exact callbacks, dropped-frame recovery, capture restart, microphone endpoint retries, nonblocking receive and routing retries passed. No audio devices opened.')


asyncio.run(main())
