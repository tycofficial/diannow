"""Real local ICE/DTLS: blocked video must not queue data-channel control."""
import asyncio,time
from aiortc import RTCPeerConnection,RTCConfiguration,RTCBundlePolicy,RTCSessionDescription,sdp
from control_transport import isolate_control

async def main():
    config=RTCConfiguration(iceServers=[],bundlePolicy=RTCBundlePolicy.BALANCED)
    viewer=RTCPeerConnection(config);desktop=RTCPeerConnection(config)
    viewer.addTransceiver('video',direction='recvonly');viewer.addTransceiver('audio',direction='recvonly')
    channel=viewer.createDataChannel('input');opened=asyncio.Event();received=asyncio.Queue()
    channel.on('open',opened.set)
    channel.on('message',received.put_nowait)
    @desktop.on('datachannel')
    def data_channel(remote):
        remote.on('message',remote.send)
    try:
        await viewer.setLocalDescription(await viewer.createOffer())
        isolated,changed=isolate_control(viewer.localDescription);assert changed
        unchanged,changed=isolate_control(viewer.localDescription,False);assert unchanged is viewer.localDescription or unchanged.sdp==viewer.localDescription.sdp
        assert not changed
        await desktop.setRemoteDescription(isolated)
        answer,changed=isolate_control(await desktop.createAnswer());assert changed
        await desktop.setLocalDescription(answer)
        await viewer.setRemoteDescription(desktop.localDescription)
        await asyncio.wait_for(opened.wait(),5)
        assert desktop.sctp.transport is not desktop.getTransceivers()[0].sender.transport
        assert viewer.sctp.transport is not viewer.getTransceivers()[0].sender.transport
        video=desktop.getTransceivers()[0].sender.transport
        blocked=asyncio.Event()
        async def blocked_video(_):await blocked.wait()
        video._send_rtp=blocked_video
        pending=asyncio.create_task(video._send_rtp(b'queued-video'))
        started=time.monotonic()
        for index in range(30):
            channel.send(str(index));assert await asyncio.wait_for(received.get(),.5)==str(index)
        assert not pending.done(),'Video must remain blocked during control test'
        blocked.set();await pending
        print({'separateControlTransport':True,'realLocalEchoes':30,'elapsedMs':round(1000*(time.monotonic()-started),1),'desktopInputInjected':False})
    finally:await asyncio.gather(viewer.close(),desktop.close())

asyncio.run(main())
