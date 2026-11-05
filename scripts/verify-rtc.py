import asyncio,json,time
from aiortc import RTCPeerConnection,RTCConfiguration,RTCSessionDescription
from aiortc.jitterbuffer import JitterBuffer
from rtc_agent import DesktopAgent
import remote_input

async def main():
    captured=[];remote_input.emit=lambda inputs:captured.extend(inputs)
    agent=DesktopAgent();client=RTCPeerConnection(RTCConfiguration(iceServers=[]));tasks=[];done=asyncio.Event();ack=asyncio.Event();observations={}
    channel=client.createDataChannel('input');client.createDataChannel('pointer',ordered=False,maxRetransmits=0)
    @channel.on('open')
    def opened():channel.send(json.dumps({'type':'key','key':'Escape','id':42}))
    @channel.on('message')
    def message(data):
        value=json.loads(data)
        if value.get('type')=='inputAck' and value.get('id')==42:
            assert not value.get('error');ack.set()
    @client.on('track')
    def on_track(track):
        async def consume():
            count=0;start=None
            try:
                while True:
                    frame=await track.recv();count+=1;start=start or time.perf_counter()
                    if count in [1,30,100]:print(json.dumps({'receivedKind':track.kind,'receivedFrames':count}),flush=True)
                    if track.kind=='video' and count==100:
                        observations['video']={'width':frame.width,'height':frame.height,'decodedFps':round(99/(time.perf_counter()-start),1)}
                    if track.kind=='audio' and count==30:observations['audio']={'sampleRate':frame.sample_rate,'channels':len(frame.layout.channels),'decodedFrames':count}
                    if 'video' in observations and 'audio' in observations:done.set()
            except Exception as error:print(json.dumps({'receiveEnded':track.kind,'error':str(error)}),flush=True)
        tasks.append(asyncio.create_task(consume()))
    video_transceiver=client.addTransceiver('video',direction='recvonly')
    # aiortc's default 128-packet receiver buffer is too small for a crisp
    # native-HD IDR. Use a bounded HD-sized buffer for this local decoder check.
    video_transceiver.receiver._RTCRtpReceiver__jitter_buffer=JitterBuffer(capacity=1024,is_video=True)
    client.addTransceiver('audio',direction='sendrecv')
    try:
        await client.setLocalDescription(await client.createOffer())
        offer={'sdp':client.localDescription.sdp,'type':client.localDescription.type,'preferences':{'quality':'native','fps':60}}
        answer=await agent.answer(offer);await client.setRemoteDescription(RTCSessionDescription(**answer))
        try:await asyncio.wait_for(done.wait(),18)
        except TimeoutError:
            stats=await client.getStats();print(json.dumps({'observations':observations,'stats':[str(value) for value in stats.values()]}),flush=True);raise
        await asyncio.wait_for(ack.wait(),5)
        assert observations['video']['width']==agent.source_width and observations['video']['height']==agent.source_height
        assert any(item.type==1 and item.ki.wScan==1 and item.ki.dwFlags==8 for item in captured)
        print(json.dumps({'rtcLoopbackVerified':True,**observations,'escAcknowledged':True,'virtualMicrophoneAvailable':bool(agent.mic_sink.device)}),flush=True)
    finally:
        for task in tasks:task.cancel()
        print('Closing RTC verifier',flush=True);await client.close();print('Closing desktop source',flush=True);await agent.close();print('RTC verifier closed',flush=True)

asyncio.run(main())
