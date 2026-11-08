"""Negotiate real H264/RTX SDP and repair beyond SRTP's old-packet window."""
import asyncio
from aiortc import RTCPeerConnection, RTCConfiguration, sdp
from aiortc.rtp import RtpPacket, wrap_rtx
from pylibsrtp import Policy, Session, Error
from rtc_agent import video_codec_preferences


async def main():
    offerer=RTCPeerConnection(RTCConfiguration(iceServers=[]))
    answerer=RTCPeerConnection(RTCConfiguration(iceServers=[]))
    try:
        offerer.addTransceiver('video',direction='recvonly')
        transceiver=answerer.addTransceiver('video',direction='sendonly')
        transceiver.setCodecPreferences(video_codec_preferences())
        await answerer.setRemoteDescription(await offerer.createOffer())
        answer=await answerer.createAnswer()
        codecs=sdp.SessionDescription.parse(answer.sdp).media[0].rtp.codecs
        primary={codec.payloadType for codec in codecs if codec.mimeType.lower()=='video/h264'}
        repairs=[codec for codec in codecs if codec.mimeType.lower()=='video/rtx']
        assert primary and len(repairs)==len(primary)
        assert {codec.parameters['apt'] for codec in repairs}==primary
        assert all(codec.mimeType.lower() in ('video/h264','video/rtx') for codec in codecs)
    finally:
        await offerer.close();await answerer.close()
    # These are the exact replay-window settings used by pinned aiortc 1.15.
    # Fixed throwaway test key; no actual traffic, devices or stored secrets.
    key=bytes(range(30))
    policy=Policy(key=key,ssrc_type=Policy.SSRC_ANY_OUTBOUND)
    policy.window_size=1024;policy.allow_repeat_tx=True
    tx=Session(policy)
    rx_policy=Policy(key=key,ssrc_type=Policy.SSRC_ANY_INBOUND)
    rx_policy.window_size=1024;rx=Session(rx_policy)
    original=RtpPacket(payload_type=102,sequence_number=0,timestamp=1000,ssrc=123,payload=b'repair-me')
    for sequence in range(1200):
        packet=RtpPacket(payload_type=102,sequence_number=sequence,timestamp=1000+sequence,ssrc=123,payload=b'repair-me')
        encrypted=tx.protect(packet.serialize())
        if sequence:rx.unprotect(encrypted)  # lose the original sequence 0
    try:rx.unprotect(tx.protect(original.serialize()))
    except Error:pass
    else:raise AssertionError('The test must exceed the real SRTP replay window')
    repair=wrap_rtx(original,payload_type=103,sequence_number=27,ssrc=456)
    recovered=RtpPacket.parse(rx.unprotect(tx.protect(repair.serialize())))
    assert recovered.payload==b'\x00\x00repair-me'
    print('Verified actual H264/RTX negotiation and encrypted late repair beyond the 1024-packet SRTP window. No network traffic.')


asyncio.run(main())
