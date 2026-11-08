"""Low-latency Windows speaker loopback. No microphone is opened."""
import asyncio, fractions, queue,time,threading,ctypes
from concurrent.futures import ThreadPoolExecutor
import av, numpy as np
import pyaudiowpatch as pyaudio
from aiortc import MediaStreamTrack
from aiortc.codecs.opus import OpusEncoder
from audio_buffer import LatestAudioFrames,PcmPlaybackBuffer


class AudioComApartment:
    """WASAPI needs COM on every calling thread, not only the first PyAudio.

    PortAudio initialization is process-global. A second PyAudio instance can
    therefore skip its COM setup when it is created on another worker. Own
    one explicit COM reference for this worker's whole device lifetime.
    """
    def __init__(self):
        self.thread=None;self.owned=False
        self.ole=ctypes.WinDLL('ole32')
        self.ole.CoInitializeEx.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
        self.ole.CoInitializeEx.restype=ctypes.c_int32
        self.ole.CoUninitialize.argtypes=[];self.ole.CoUninitialize.restype=None
    def open(self):
        result=self.ole.CoInitializeEx(None,0)  # COINIT_MULTITHREADED
        if result<0 and result!=-2147417850:  # RPC_E_CHANGED_MODE: existing STA
            raise OSError('Audio worker COM initialization failed: 0x'+format(result&0xffffffff,'08x'))
        self.thread=threading.get_ident();self.owned=result>=0
    def close(self):
        if self.owned:
            if threading.get_ident()!=self.thread:raise RuntimeError('Audio COM cleanup must stay on its owning thread')
            self.ole.CoUninitialize();self.owned=False


class TimelineOpusEncoder(OpusEncoder):
    """Keep a capture gap from generating an extra stale RTP timestamp."""
    def __init__(self):
        super().__init__();self.configure();self.expected_time=None;self.rtp_origin=None;self.lookahead=None
    def configure(self):
        self.codec.options={'application':'lowdelay','frame_duration':'5'}
        self.resampler=av.AudioResampler(format='s16',layout='stereo',rate=48000,frame_size=240)
    def encode(self,frame,force_keyframe=False):
        frame_time=frame.pts*frame.time_base if frame.pts is not None and frame.time_base is not None else None
        if self.rtp_origin is None and frame_time is not None:self.rtp_origin=round(frame_time*48000)
        if frame_time is not None and self.expected_time is not None and abs(frame_time-self.expected_time)>fractions.Fraction(1,frame.sample_rate):
            # libopus retains its lookahead across an input-PTS jump. Without
            # a reset it stamps the first fresh packet with the old expected
            # PTS and only jumps on the next packet, adding two large jitter
            # spikes. Preserve the RTP origin while dropping stale lookahead.
            origin=self.first_packet_pts
            OpusEncoder.__init__(self)
            self.configure()
            self.lookahead=None
            self.first_packet_pts=origin
        self.expected_time=frame_time+fractions.Fraction(frame.samples,frame.sample_rate) if frame_time is not None else None
        packets=[]
        for converted in self.resampler.resample(frame):
            encoded=self.codec.encode(converted)
            if encoded and self.lookahead is None:self.lookahead=converted.pts-encoded[0].pts
            packets.extend(encoded)
        if not packets:return [],None
        stamp=packets[0].pts+(self.lookahead or 0)-(self.rtp_origin or 0)
        return [bytes(packet) for packet in packets],stamp

class DesktopAudioTrack(MediaStreamTrack):
    kind='audio'
    def __init__(self):
        super().__init__();self.audio=None;self.stream=None;self.closed=False;self.error=None;self.recoveries=0;self.independent=False
        self.frames=queue.Queue(maxsize=2);self.timestamp=0;self.started=time.monotonic();self.capture_position=0;self.generation=0
        self.lifecycle_lock=threading.Lock();self.last_capture=self.started;self.next_recovery=0
        self.device_thread=None;self.apartment=None
        self.reader=ThreadPoolExecutor(max_workers=1,thread_name_prefix='DesktopAudioRead')
        try:self.reader.submit(self._start_capture).result()
        except BaseException:
            self.closed=True
            try:self.reader.submit(self._shutdown_capture).result()
            finally:self.reader.shutdown(wait=False,cancel_futures=True)
            raise
    def _start_capture(self):
        self.device_thread=threading.get_ident()
        self.apartment=AudioComApartment();self.apartment.open()
        try:self._open_capture()
        except Exception as error:
            # Keep the negotiated audio track alive through endpoint startup
            # failures. Previously one transient open error omitted the audio
            # sender for the entire session, so later recovery never ran.
            self.error=type(error).__name__+': '+str(error)
            try:self._close_capture()
            except Exception:pass
            self.last_capture=self.started-2
    def _shutdown_capture(self):
        try:
            with self.lifecycle_lock:self._close_capture()
        finally:
            if self.apartment:self.apartment.close()
    def _open_capture(self):
        self.audio=pyaudio.PyAudio()
        devices=list(self.audio.get_device_info_generator())
        virtual=[d for d in devices if d['maxInputChannels']>0 and not d.get('isLoopbackDevice') and 'hi-fi cable output' in d['name'].lower()]
        wasapi=self.audio.get_host_api_info_by_type(pyaudio.paWASAPI)['index']
        virtual.sort(key=lambda d:d['hostApi']!=wasapi)
        self.independent=bool(virtual)
        self.device=virtual[0] if virtual else self.audio.get_default_wasapi_loopback()
        self.rate=int(self.device['defaultSampleRate']);self.channels=min(2,int(self.device['maxInputChannels']));self.samples=self.rate//200
        try:
            if self.audio.is_format_supported(48000,input_device=int(self.device['index']),input_channels=self.channels,input_format=pyaudio.paInt16):self.rate=48000;self.samples=240
        except ValueError:pass
        self.generation+=1;generation=self.generation
        # A reopened endpoint may have a different sample rate. Keep its PTS
        # on the original monotonic timeline instead of replaying old audio.
        self.capture_position=max(round((time.monotonic()-self.started)*self.rate),round(self.timestamp*self.rate/getattr(self,'timestamp_rate',self.rate)))
        self.last_capture=time.monotonic()
        rate=self.rate;channels=self.channels
        def callback(data,frame_count,time_info,status):
            if self.closed or generation!=self.generation:return (None,pyaudio.paComplete)
            self.last_capture=time.monotonic()
            while self.frames.full():
                try:self.frames.get_nowait()
                except queue.Empty:break
            try:self.frames.put_nowait((data,self.capture_position,rate,channels))
            except queue.Full:pass
            self.capture_position+=frame_count
            return (None,pyaudio.paContinue)
        self.stream=self.audio.open(format=pyaudio.paInt16,channels=self.channels,rate=self.rate,input=True,input_device_index=int(self.device['index']),frames_per_buffer=self.samples,stream_callback=callback)
    def _close_capture(self):
        self.generation+=1
        stream,self.stream=self.stream,None;audio,self.audio=self.audio,None
        try:
            if stream:stream.close()
        finally:
            if audio:audio.terminate()
    def _recover_capture(self):
        if self.closed:return
        if threading.get_ident()!=self.device_thread:
            return self.reader.submit(self._recover_capture).result()
        if time.monotonic()<self.next_recovery:return
        with self.lifecycle_lock:
            if self.closed:return
            self.next_recovery=time.monotonic()+1
            try:
                self._close_capture()
                while not self.frames.empty():
                    try:self.frames.get_nowait()
                    except queue.Empty:break
                self._open_capture();self.recoveries+=1;self.error=None
            except Exception as error:
                self.error=type(error).__name__+': '+str(error)
                try:self._close_capture()
                except Exception:pass
    async def recv(self):
        # The WASAPI capture callback is the audio clock. A second wall-clock
        # pacer used to generate silence between bursts and repeatedly reset
        # the 44.1kHz resampler under load. Wait for actual captured samples.
        reader=getattr(self,'reader',None)
        item=await asyncio.get_running_loop().run_in_executor(reader,self.read) if reader else await asyncio.to_thread(self.read)
        data,timestamp=item[:2];rate,channels=item[2:] if len(item)>2 else (self.rate,self.channels)
        samples=np.frombuffer(data,dtype=np.int16).reshape(1,-1)
        frame=av.AudioFrame.from_ndarray(samples,format='s16',layout='stereo' if channels==2 else 'mono')
        frame.sample_rate=rate;frame.pts=timestamp;frame.time_base=fractions.Fraction(1,rate);self.timestamp=timestamp+frame.samples;self.timestamp_rate=rate
        return frame
    def read(self):
        while not self.closed:
            try:
                item=self.frames.get(timeout=.1)
                # At most two capture blocks are retained. Never drain a
                # historical device backlog after the sender was delayed.
                if self.frames.full():item=self.frames.get_nowait()
                return item
            except queue.Empty:
                # Lost endpoint / stopped WASAPI callbacks used to leave the
                # sender awaiting this queue forever, even after reconnection.
                if hasattr(self,'last_capture') and time.monotonic()-self.last_capture>1:self._recover_capture()
        from aiortc.mediastreams import MediaStreamError
        raise MediaStreamError
    def stop(self):
        if self.closed:return
        self.closed=True;super().stop()
        try:
            if threading.get_ident()==self.device_thread:self._shutdown_capture()
            else:self.reader.submit(self._shutdown_capture).result()
        finally:self.reader.shutdown(wait=False,cancel_futures=True)

def virtual_microphone_device(audio):
    devices=[device for device in audio.get_device_info_generator() if device['maxOutputChannels']>0 and not device.get('isLoopbackDevice') and 'hi-fi' not in device['name'].lower() and any(name in device['name'].lower() for name in ['cable input','voicemeeter input','virtual cable','virtual audio cable'])]
    wasapi=audio.get_host_api_info_by_type(pyaudio.paWASAPI)['index']
    devices.sort(key=lambda device:device['hostApi']!=wasapi)
    return devices[0] if devices else None

class MicrophoneSink:
    def __init__(self):
        self.audio=None;self.device=None;self.stream=None;self.closed=False;self.error=None;self.recoveries=0
        self.frames=LatestAudioFrames(3);self.playback=None;self.retry_at=0;self.retry_delay=.1;self.last_frame_time=None
        self.output_generation=0;self.last_callback=0;self.callback_stalls=0;self.callback_statuses=0
        # All resampling and PortAudio ownership stays on one dedicated
        # thread. A slow device cannot stall the receiver or video executor.
        self.worker=threading.Thread(target=self._run,name='VirtualMicrophone',daemon=True);self.worker.start()
    def _close_output(self):
        self.output_generation+=1
        stream,self.stream=self.stream,None;audio,self.audio=self.audio,None
        self.device=None
        try:
            if stream:stream.close()
        finally:
            if audio:audio.terminate()
    def _open_output(self):
        self.audio=pyaudio.PyAudio();self.device=virtual_microphone_device(self.audio)
        if not self.device:raise RuntimeError('No virtual microphone playback endpoint is available')
        self.rate=int(self.device['defaultSampleRate']);self.channels=min(2,int(self.device['maxOutputChannels']))
        self.playback=PcmPlaybackBuffer(round(self.rate*.04)*self.channels*2,self.channels*2)
        self.resampler=av.AudioResampler(format='s16',layout='stereo' if self.channels==2 else 'mono',rate=self.rate)
        self.last_frame_time=None
        self.output_generation+=1;generation=self.output_generation
        playback=self.playback;channels=self.channels;self.last_callback=time.monotonic()
        def callback(data,frame_count,time_info,status):
            # A delayed callback from a removed endpoint must neither drain
            # fresh PCM nor make a new, stalled endpoint appear healthy.
            if self.closed or generation!=self.output_generation:return (bytes(frame_count*channels*2),pyaudio.paComplete)
            self.last_callback=time.monotonic()
            if status:self.callback_statuses+=1
            return (playback.read(frame_count),pyaudio.paContinue)
        self.stream=self.audio.open(format=pyaudio.paInt16,channels=self.channels,rate=self.rate,output=True,output_device_index=int(self.device['index']),frames_per_buffer=max(1,self.rate//200),stream_callback=callback)
        self.retry_delay=.1;self.error=None;self.recoveries+=1
    def _run(self):
        dropped=0;apartment=None
        try:
            apartment=AudioComApartment();apartment.open()
            while not self.closed:
                frame=self.frames.get(.1)
                try:
                    if self.stream and not self.stream.is_active():raise OSError('Virtual microphone playback stopped')
                    # Some WASAPI failures keep reporting active after the
                    # callbacks have stopped. Previously that left a live
                    # receiver feeding a permanently silent output forever.
                    if self.stream and time.monotonic()-self.last_callback>1:
                        self.callback_stalls+=1
                        raise OSError('Virtual microphone playback callbacks stalled')
                    if not self.stream:
                        if time.monotonic()<self.retry_at:continue
                        self._open_output()
                    if frame is None:continue
                    frame_time=frame.pts*frame.time_base if frame.pts is not None and frame.time_base is not None else None
                    if dropped!=self.frames.dropped or (self.last_frame_time is not None and frame_time is not None and abs(frame_time-self.last_frame_time)>fractions.Fraction(1,frame.sample_rate)):
                        self.resampler=av.AudioResampler(format='s16',layout='stereo' if self.channels==2 else 'mono',rate=self.rate)
                        self.playback.clear()
                    dropped=self.frames.dropped
                    self.last_frame_time=frame_time+fractions.Fraction(frame.samples,frame.sample_rate) if frame_time is not None else None
                    for converted in self.resampler.resample(frame):self.playback.put(converted.to_ndarray().tobytes())
                except Exception as error:
                    self.error=type(error).__name__+': '+str(error)
                    try:self._close_output()
                    except Exception:pass
                    self.retry_at=time.monotonic()+self.retry_delay;self.retry_delay=min(2,self.retry_delay*2)
        except Exception as error:self.error=type(error).__name__+': '+str(error)
        finally:
            try:self._close_output()
            except Exception:pass
            if apartment:apartment.close()
    async def consume(self,track):
        try:
            while not self.closed:
                frame=await track.recv()
                self.frames.put(frame)
        except asyncio.CancelledError:raise
        except Exception as error:
            self.error=type(error).__name__+': '+str(error)
        finally:
            # consume runs on the media event loop; the device worker owns
            # teardown and explicit close() can join it off that loop.
            self.closed=True;self.frames.close()
    def close(self):
        self.closed=True;self.frames.close()
        if threading.current_thread() is not self.worker:self.worker.join(timeout=2)
