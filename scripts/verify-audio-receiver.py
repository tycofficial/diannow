"""Real Opus decode with blocked consumers and bounded receive-side backlog.

No sound endpoints, display capture or Internet traffic are used. The real
aiortc receiver launches its original libopus worker; only its queues change.
"""
import asyncio
import fractions
import json
import threading
import time
import tracemalloc

import av
import numpy as np
from aiortc import RTCPeerConnection, RTCConfiguration
from aiortc.codecs.opus import OpusEncoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.mediastreams import MediaStreamError
from aiortc.rtcrtpparameters import RTCRtpCodecParameters, RTCRtpReceiveParameters
from aiortc.rtcrtpreceiver import RemoteStreamTrack

from audio_receiver import LatestDecoderPackets, LatestDecodedFrames, install_audio_receive_buffers


async def until(predicate, timeout=3):
    deadline=time.monotonic()+timeout
    while not predicate():
        assert time.monotonic()<deadline,'Real audio decode did not progress'
        await asyncio.sleep(.005)


def packet():
    samples=np.sin(2*np.pi*440*np.arange(960)/48000)*12000
    samples=np.repeat(samples.astype(np.int16),2).reshape(1,-1)
    source=av.AudioFrame.from_ndarray(samples,format='s16',layout='stereo')
    source.sample_rate=48000;source.pts=0;source.time_base=fractions.Fraction(1,48000)
    encoded,_=OpusEncoder().encode(source)
    assert len(encoded)==1
    return encoded[0]


async def main():
    # A paused native decoder retains three packets, not seconds of history.
    # Measure live Python allocations with distinct payload bytes, rather
    # than repeatedly enqueueing one shared object that hides memory growth.
    bounded=LatestDecoderPackets(3)
    tracemalloc.start()
    for index in range(20000):
        bounded.put((index,bytes([index%256])*1200))
    current,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert bounded.qsize()==3 and bounded.dropped==19997
    assert current<100000 and peak<150000,(current,peak)
    assert [bounded.get_nowait()[0] for _ in range(3)]==[19997,19998,19999]

    # Closing a saturated queue wakes a worker and cannot lose its sentinel.
    for index in range(10):bounded.put(index)
    bounded.put(None);bounded.put(999)
    assert bounded.get_nowait() is None and bounded.empty()
    blocked=LatestDecoderPackets(3);ended=threading.Event()
    waiter=threading.Thread(target=lambda:(blocked.get(),ended.set()))
    waiter.start();blocked.put(None);waiter.join(1);assert ended.is_set()

    peer=RTCPeerConnection(RTCConfiguration(iceServers=[]))
    receiver=peer.addTransceiver('audio',direction='recvonly').receiver
    receiver._track=RemoteStreamTrack('audio')
    buffers=install_audio_receive_buffers(receiver)
    assert buffers is not None and install_audio_receive_buffers(receiver) is buffers
    codec=RTCRtpCodecParameters(mimeType='audio/opus',clockRate=48000,channels=2,payloadType=111)
    encoded=packet()
    try:
        # Native worker starts only after the backlog is installed. Only the
        # newest 60 ms reach libopus; output timestamps remain truthful.
        for index in range(10000):
            buffers.encoded.put((codec,JitterFrame(encoded,index*960)))
        await receiver.receive(RTCRtpReceiveParameters(codecs=[codec]))
        await until(lambda:buffers.decoded.qsize()==3)
        first=await receiver.track.recv()
        assert first.pts==9997*960 and first.samples==960
        energy=float(np.sqrt(np.mean(first.to_ndarray().astype(np.float64)**2)))
        assert energy>500,energy
        while not buffers.decoded.empty():await receiver.track.recv()

        # Let decode run normally while the sink stops consuming. Decoded
        # PCM must remain bounded too, and resume with fresh audible samples.
        for index in range(10000,10040):
            buffers.encoded.put((codec,JitterFrame(encoded,index*960)))
            await until(lambda:buffers.decoded.dropped+buffers.decoded.qsize()>=index-9999)
        await until(lambda:buffers.encoded.empty() and buffers.decoded.dropped>=37)
        assert buffers.decoded.qsize()==3
        stamps=[]
        for _ in range(3):
            decoded=await receiver.track.recv();stamps.append(decoded.pts)
            assert float(np.sqrt(np.mean(decoded.to_ndarray().astype(np.float64)**2)))>500
        assert stamps==[10037*960,10038*960,10039*960],stamps
        # A waiting consumer is woken by a subsequent real decoded frame.
        pending=asyncio.create_task(receiver.track.recv());await asyncio.sleep(.01)
        assert not pending.done()
        buffers.encoded.put((codec,JitterFrame(encoded,10040*960)))
        assert (await asyncio.wait_for(pending,1)).pts==10040*960
        await receiver.stop()
        try:await asyncio.wait_for(receiver.track.recv(),1)
        except MediaStreamError:pass
        else:raise AssertionError('Audio receiver termination was swallowed')
        print(json.dumps({'realAiortcOpusDecoder':True,'encodedPacketLimit':3,
            'decodedFrameLimit':3,'distinctQueuedPayloads':20000,
            'retainedPythonBytes':current,'peakPythonBytes':peak,
            'resumedSignalRms':round(energy,1),'queues':buffers.stats,
            'soundDevicesOpened':False,'networkTraffic':False}))
    finally:
        await peer.close()


asyncio.run(main())
