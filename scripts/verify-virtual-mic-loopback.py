"""Short, real VB-Audio Virtual Cable test; never use physical audio devices.

This explicitly opens ONLY the WASAPI VB-Audio Virtual Cable playback and
capture endpoints, sends one second of low-amplitude generated 997 Hz through
real Opus encode/decode and MicrophoneSink, and measures the cable's return.
It does not open a physical microphone/speaker, change defaults, mute devices,
or send network traffic. A missing/ambiguous virtual endpoint is a failure.
Run serially with other audio/media probes. All owned streams close on exit.
"""
import asyncio
import fractions
import json
import queue
import time

import av
import numpy as np
import pyaudiowpatch as pyaudio
from aiortc.codecs.opus import OpusEncoder, OpusDecoder
from aiortc.jitterbuffer import JitterFrame

from desktop_audio import MicrophoneSink, virtual_microphone_device


async def until(predicate, timeout=4):
    deadline=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>=deadline:raise TimeoutError('Virtual cable test did not become ready')
        await asyncio.sleep(.01)


def validate_pair(audio):
    wasapi=audio.get_host_api_info_by_type(pyaudio.paWASAPI)['index']
    output=virtual_microphone_device(audio)
    if not output or output['hostApi']!=wasapi or output.get('isLoopbackDevice') or not output['name'].lower().endswith('(vb-audio virtual cable)'):
        raise RuntimeError('Production microphone output is not an explicit WASAPI VB-Audio Virtual Cable endpoint')
    inputs=[device for device in audio.get_device_info_generator()
        if device['hostApi']==wasapi and device['maxInputChannels']>0
        and not device.get('isLoopbackDevice')
        and device['name'].lower()=='cable output (vb-audio virtual cable)']
    if len(inputs)!=1:raise RuntimeError('One unambiguous WASAPI CABLE Output capture endpoint is required')
    return output,inputs[0]


class GeneratedOpusTrack:
    def __init__(self):
        self.encoder=OpusEncoder();self.decoder=OpusDecoder();self.count=0;self.started=None
        self.finished=asyncio.Event()

    async def recv(self):
        if self.count>=50:
            self.finished.set()
            await asyncio.Event().wait()
        if self.started is None:self.started=time.monotonic()
        await asyncio.sleep(max(0,self.started+self.count*.02-time.monotonic()))
        pts=self.count*960
        wave=(np.sin(2*np.pi*997*(np.arange(960)+pts)/48000)*2000).astype(np.int16)
        pcm=np.repeat(wave,2).reshape(1,-1)
        frame=av.AudioFrame.from_ndarray(pcm,format='s16',layout='stereo')
        frame.sample_rate=48000;frame.pts=pts;frame.time_base=fractions.Fraction(1,48000)
        packets,stamp=self.encoder.encode(frame)
        if len(packets)!=1:raise RuntimeError('Unexpected Opus batching in loopback source')
        decoded=self.decoder.decode(JitterFrame(packets[0],stamp))
        if len(decoded)!=1:raise RuntimeError('Unexpected Opus decode output in loopback source')
        self.count+=1
        return decoded[0]


async def main():
    audio=pyaudio.PyAudio();capture=None;sink=None;task=None
    captured=queue.Queue(maxsize=600);callback_times=[];callback_statuses=[];overflows=0
    try:
        output,input_device=validate_pair(audio)
        rate=int(input_device['defaultSampleRate']);channels=min(2,int(input_device['maxInputChannels']))
        def record(data,frame_count,time_info,status):
            nonlocal overflows
            try:captured.put_nowait(data)
            except queue.Full:overflows+=1
            callback_times.append(time.monotonic())
            if status:callback_statuses.append(int(status))
            return (None,pyaudio.paContinue)
        capture=audio.open(format=pyaudio.paInt16,channels=channels,rate=rate,input=True,
            input_device_index=int(input_device['index']),frames_per_buffer=max(1,rate//200),
            stream_callback=record)
        sink=MicrophoneSink()
        try:await until(lambda:sink.stream is not None and sink.last_callback>0)
        except TimeoutError as error:
            raise TimeoutError('Virtual microphone output failed to open: '+json.dumps({
                'error':sink.error,'workerAlive':sink.worker.is_alive(),
                'device':sink.device['name'] if sink.device else None,
                'recoveries':sink.recoveries,'callbackStalls':sink.callback_stalls},ensure_ascii=True)) from error
        if sink.device['index']!=output['index']:
            raise RuntimeError('Production microphone sink changed to an unexpected endpoint')
        await asyncio.sleep(.15)
        source=GeneratedOpusTrack();task=asyncio.create_task(sink.consume(source))
        await asyncio.wait_for(source.finished.wait(),4)
        await asyncio.sleep(.25)
        task.cancel();await asyncio.gather(task,return_exceptions=True);task=None
        await asyncio.to_thread(sink.close)
        capture.close();capture=None
        blocks=[]
        while not captured.empty():blocks.append(captured.get_nowait())
        if not blocks:raise AssertionError('No virtual cable return samples')
        samples=np.frombuffer(b''.join(blocks),dtype=np.int16).reshape(-1,channels).astype(np.float64).mean(axis=1)
        block_size=max(1,rate//100)
        chunks=samples[:len(samples)//block_size*block_size].reshape(-1,block_size)
        rms=np.sqrt(np.mean(chunks**2,axis=1))
        phase=2*np.pi*997*np.arange(block_size)/rate
        tone=np.abs(chunks@np.exp(-1j*phase))*2/block_size
        active=np.flatnonzero(tone>200)
        if active.size:
            interior=active[-1]-active[0]+1
            mask=tone[active[0]:active[-1]+1]<=200
            longest=run=0
            for silent in mask:
                run=run+1 if silent else 0;longest=max(longest,run)
            maximum_gap_ms=longest*block_size/rate*1000
        else:interior=0;maximum_gap_ms=None
        callback_gaps=np.diff(callback_times)*1000
        result={'actualVirtualCable':True,'realOpusEncodeDecode':True,
            'output':output['name'],'capture':input_device['name'],'sampleRate':rate,
            'generatedSeconds':1,'capturedSeconds':round(len(samples)/rate,3),
            'toneDetectedSeconds':round(len(active)*block_size/rate,3),
            'activeRmsMedian':round(float(np.median(rms[active])),1) if active.size else 0,
            'maximumInteriorToneGapMs':round(maximum_gap_ms,1) if maximum_gap_ms is not None else None,
            'maximumCaptureCallbackGapMs':round(float(max(callback_gaps)),1) if len(callback_gaps) else None,
            'callbackStatusCount':len(callback_statuses),'captureQueueOverflows':overflows,
            'sinkFrameDrops':sink.frames.dropped,'sinkCallbackStalls':sink.callback_stalls,
            'physicalDevicesOpened':False,'defaultDevicesChanged':False,'networkTraffic':False}
        result['passed']=bool(len(active)*block_size/rate>=.7 and maximum_gap_ms is not None and maximum_gap_ms<=100 and not overflows and not sink.callback_stalls)
        print(json.dumps(result,ensure_ascii=True))
        if not result['passed']:raise AssertionError('Actual virtual microphone return failed signal/continuity checks')
    finally:
        if task:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        if sink:await asyncio.to_thread(sink.close)
        if capture:capture.close()
        audio.terminate()


if __name__=='__main__':asyncio.run(main())
