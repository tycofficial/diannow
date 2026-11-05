"""Verify SDP and RTCP identities without opening desktop or network IO."""
import asyncio
import contextlib
import io
import json
import random
from types import SimpleNamespace

from aiortc import RTCPeerConnection,RTCConfiguration,MediaStreamTrack,sdp
from aiortc.rtp import RtcpSdesPacket
from rtc_agent import independent_media_sender,independent_media_description,DesktopSession


class NoMediaTrack(MediaStreamTrack):
    async def recv(self):raise AssertionError('The identity check must never read media.')


async def main():
    mock_calls=[]
    async def original_send(parameters):mock_calls.append(parameters.rtcp.cname)
    mock=SimpleNamespace(send=original_send)
    independent_media_sender(mock,'mock-video')
    await mock.send(SimpleNamespace(rtcp=SimpleNamespace(cname='shared')))
    assert mock_calls==['mock-video'] and mock._stream_id=='mock-video'

    pc=RTCPeerConnection(RTCConfiguration(iceServers=[]));senders={};names={'video':'test-video','audio':'test-audio'}
    try:
        for kind in names:
            track=NoMediaTrack();track.kind=kind
            sender=pc.addTrack(track);independent_media_sender(sender,names[kind]);senders[kind]=sender
        description=independent_media_description(await pc.createOffer(),names)
        parsed=sdp.SessionDescription.parse(description.sdp)
        for media in parsed.media:
            assert media.msid.split()[0]==names[media.kind]
            assert all(source.cname==names[media.kind] for source in media.ssrc)

        # Drive the real aiortc sender send() and RTCP SDES generator with its
        # network tasks replaced by controlled waiters, then inspect packets.
        for kind,sender in senders.items():
            transceiver=next(t for t in pc.getTransceivers() if t.sender is sender)
            parameters=pc._RTCPeerConnection__localRtp(transceiver)
            async def wait_forever(_codec):await asyncio.Event().wait()
            async def wait_rtcp():await asyncio.Event().wait()
            sender._run_rtp=wait_forever;sender._run_rtcp=wait_rtcp
            await sender.send(parameters)
            sender._RTCRtpSender__rtp_task.cancel();sender._RTCRtpSender__rtcp_task.cancel()
            await asyncio.gather(sender._RTCRtpSender__rtp_task,sender._RTCRtpSender__rtcp_task,return_exceptions=True)
            assert sender._RTCRtpSender__cname==names[kind]
            captured=[]
            async def capture(packets):
                captured.extend(packets)
                if any(isinstance(packet,RtcpSdesPacket) for packet in packets):raise asyncio.CancelledError
            sender._send_rtcp=capture
            actual_rtcp=type(sender)._run_rtcp
            original_random=random.random;random.random=lambda:0
            try:await actual_rtcp(sender)
            finally:random.random=original_random
            emitted=[packet for packet in captured if isinstance(packet,RtcpSdesPacket)]
            assert emitted and emitted[0].chunks[0].items==[(1,names[kind].encode())]
            sender._RTCRtpSender__started=False

        session=DesktopSession.__new__(DesktopSession)
        from collections import deque
        session.fps_samples=deque();session.session_id='report-test';session.last_report_log=0
        track=SimpleNamespace(sent=0)
        assert session.recent_fps(track,0)==0
        track.sent=300;assert session.recent_fps(track,5)==60
        track.sent=320;assert session.recent_fps(track,6)==20
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            session.report_receiver({'receiverReport':{'fps':59,'bufferMs':12,'mbps':float('inf'),'decodeMs':'bad'}},now=10)
            session.report_receiver({'receiverReport':{'fps':10}},now=11)
        reports=output.getvalue().splitlines();assert len(reports)==1
        assert json.loads(reports[0])['receiverReport']=={'fps':59,'bufferMs':12}
        print(json.dumps({'differentAudioVideoMsid':True,'sdpMatchesActualRtcpCname':True,'sendMockVerified':True,'recentFpsAndReportThrottleVerified':True,'desktopIO':False,'networkTraffic':False}))
    finally:
        for sender in senders.values():sender._RTCRtpSender__started=False
        await pc.close()


asyncio.run(main())
