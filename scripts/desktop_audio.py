"""Low-latency Windows speaker loopback. No microphone is opened."""
import asyncio, fractions, queue,time
import av, numpy as np
import pyaudiowpatch as pyaudio
from aiortc import MediaStreamTrack
from aiortc.codecs.opus import OpusEncoder

class TimelineOpusEncoder(OpusEncoder):
    """Keep a capture gap from generating an extra stale RTP timestamp."""
    def __init__(self):
        super().__init__();self.expected_time=None
    def encode(self,frame,force_keyframe=False):
        frame_time=frame.pts*frame.time_base if frame.pts is not None and frame.time_base is not None else None
        if frame_time is not None and self.expected_time is not None and abs(frame_time-self.expected_time)>fractions.Fraction(1,frame.sample_rate):
            # libopus retains its lookahead across an input-PTS jump. Without
            # a reset it stamps the first fresh packet with the old expected
            # PTS and only jumps on the next packet, adding two large jitter
            # spikes. Preserve the RTP origin while dropping stale lookahead.
            origin=self.first_packet_pts
            OpusEncoder.__init__(self)
            self.first_packet_pts=origin
        self.expected_time=frame_time+fractions.Fraction(frame.samples,frame.sample_rate) if frame_time is not None else None
        return super().encode(frame,force_keyframe)

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
        if self.started is None:self.started=time.perf_counter()
        pause=self.started+self.timestamp/self.rate-time.perf_counter()
        if pause>0:await asyncio.sleep(pause)
        # After a busy event loop resumes, send current audio instead of
        # rapidly synthesizing all the missed 20 ms frames to catch up.
        late_samples=int(max(0,time.perf_counter()-self.started-self.timestamp/self.rate)*self.rate)
        if late_samples>=self.samples*2:
            self.timestamp+=(late_samples//self.samples)*self.samples
            while self.frames.qsize()>1:
                try:self.frames.get_nowait()
                except queue.Empty:break
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
