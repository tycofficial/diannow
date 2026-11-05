"""Low-latency Windows speaker loopback. No microphone is opened."""
import asyncio, fractions, queue,time
import av, numpy as np
import pyaudiowpatch as pyaudio
from aiortc import MediaStreamTrack

class DesktopAudioTrack(MediaStreamTrack):
    kind='audio'
    def __init__(self):
        super().__init__();self.audio=pyaudio.PyAudio();self.device=self.audio.get_default_wasapi_loopback()
        self.rate=int(self.device['defaultSampleRate']);self.channels=min(2,int(self.device['maxInputChannels']));self.samples=self.rate//50
        self.frames=queue.Queue(maxsize=2);self.timestamp=0;self.closed=False;self.started=None
        def callback(data,frame_count,time_info,status):
            if self.closed:return (None,pyaudio.paComplete)
            while self.frames.full():
                try:self.frames.get_nowait()
                except queue.Empty:break
            self.frames.put_nowait(data)
            return (None,pyaudio.paContinue)
        self.stream=self.audio.open(format=pyaudio.paInt16,channels=self.channels,rate=self.rate,input=True,input_device_index=int(self.device['index']),frames_per_buffer=self.samples,stream_callback=callback)
    async def recv(self):
        self.started=self.started or time.perf_counter()
        pause=self.started+self.timestamp/self.rate-time.perf_counter()
        if pause>0:await asyncio.sleep(pause)
        try:data=self.frames.get_nowait()
        except queue.Empty:data=bytes(self.samples*self.channels*2)
        samples=np.frombuffer(data,dtype=np.int16).reshape(1,-1)
        frame=av.AudioFrame.from_ndarray(samples,format='s16',layout='stereo' if self.channels==2 else 'mono')
        frame.sample_rate=self.rate;frame.pts=self.timestamp;frame.time_base=fractions.Fraction(1,self.rate);self.timestamp+=frame.samples
        return frame
    def read(self):
        try:return self.frames.get(timeout=.06)
        except queue.Empty:return bytes(self.samples*self.channels*2)
    def stop(self):
        if self.closed:return
        self.closed=True;super().stop()
        if getattr(self,'stream',None):self.stream.stop_stream();self.stream.close()
        self.audio.terminate()

def virtual_microphone_device(audio):
    devices=[device for device in audio.get_device_info_generator() if device['maxOutputChannels']>0 and not device.get('isLoopbackDevice') and any(name in device['name'].lower() for name in ['cable input','voicemeeter input','virtual cable','virtual audio cable'])]
    devices.sort(key=lambda device:device['hostApi']!=2)
    return devices[0] if devices else None

class MicrophoneSink:
    def __init__(self):
        self.audio=pyaudio.PyAudio();self.device=virtual_microphone_device(self.audio);self.stream=None;self.closed=False
        if self.device:
            self.rate=int(self.device['defaultSampleRate']);self.channels=min(2,int(self.device['maxOutputChannels']))
            self.stream=self.audio.open(format=pyaudio.paInt16,channels=self.channels,rate=self.rate,output=True,output_device_index=int(self.device['index']),frames_per_buffer=self.rate//50)
            self.resampler=av.AudioResampler(format='s16',layout='stereo' if self.channels==2 else 'mono',rate=self.rate)
    async def consume(self,track):
        try:
            while not self.closed:
                frame=await track.recv()
                if self.stream:
                    for converted in self.resampler.resample(frame):
                        data=converted.to_ndarray().tobytes()
                        await asyncio.to_thread(self.stream.write,data,exception_on_underflow=False)
        except Exception:
            pass
    def close(self):
        self.closed=True
        if self.stream:self.stream.stop_stream();self.stream.close()
        self.audio.terminate()
