"""Check negotiated playout-delay metadata without media or network traffic."""
import asyncio
import json
import struct

from aiortc import RTCPeerConnection,RTCConfiguration,RTCSessionDescription,MediaStreamTrack,sdp
from aiortc.rtp import RtpPacket,unpack_header_extensions
from low_latency_rtp import PLAYOUT_DELAY_URI,configure_video_playout_delay,playout_delay_bytes


class NoMediaTrack(MediaStreamTrack):
    async def recv(self):raise AssertionError('No media should be read.')


def raw_extensions(data):
    offset=12+4*(data[0]&15)
    if not data[0]&16:return []
    profile,words=struct.unpack_from('!HH',data,offset)
    return unpack_header_extensions(profile,data[offset+4:offset+4+4*words])


async def scenario(identifier=None,direction='',collision=False):
    offerer=RTCPeerConnection(RTCConfiguration(iceServers=[]));answerer=RTCPeerConnection(RTCConfiguration(iceServers=[]));calls={}
    try:
        offerer.addTransceiver('video',direction='recvonly');offerer.addTransceiver('audio',direction='recvonly')
        offer=await offerer.createOffer();parsed=sdp.SessionDescription.parse(offer.sdp)
        video=next(media for media in parsed.media if media.kind=='video')
        marker='a=mid:'+video.rtp.muxId
        extension_line='a=extmap:'+str(identifier)+('/'+direction if direction else '')+' '+PLAYOUT_DELAY_URI if identifier is not None else ''
        text=offer.sdp.replace(marker,marker+'\r\n'+extension_line) if extension_line else offer.sdp
        if collision:text=text.replace(marker,marker+'\r\na=extmap:'+str(identifier)+' urn:example:conflict')
        for kind in ['video','audio']:
            track=NoMediaTrack();track.kind=kind;sender=answerer.addTrack(track)
            async def send(parameters,sender=sender,kind=kind):
                # Observe the exact parameters and real extension mapper used
                # by aiortc when serializing outgoing RTP.
                calls[kind]=parameters
                sender._RTCRtpSender__rtp_header_extensions_map.configure(parameters)
            sender.send=send
        await answerer.setRemoteDescription(RTCSessionDescription(sdp=text,type='offer'))
        enabled=configure_video_playout_delay(answerer,text)
        answer=sdp.SessionDescription.parse((await answerer.createAnswer()).sdp)
        transceivers={item.kind:item for item in answerer.getTransceivers()}
        for transceiver in transceivers.values():await transceiver.sender.send(answerer._RTCPeerConnection__localRtp(transceiver))
        supported=identifier is not None and direction!='sendonly' and not collision
        assert bool(enabled)==supported
        for kind,transceiver in transceivers.items():
            extensions=next(media.rtp.headerExtensions for media in answer.media if media.kind==kind)
            negotiated=[extension for extension in extensions if extension.uri==PLAYOUT_DELAY_URI]
            assert bool(negotiated)==(supported and kind=='video')
            assert [(e.id,e.uri) for e in calls[kind].headerExtensions]==[(e.id,e.uri) for e in extensions]
            packet=RtpPacket(payload_type=102 if kind=='video' else 111,sequence_number=4321,timestamp=123456,ssrc=0x12345678,payload=b'unchanged-payload')
            packet.marker=1;packet.csrc=[0x76543210];packet.extensions.mid=transceiver.mid;packet.extensions.abs_send_time=0x123456
            mapper=transceiver.sender._RTCRtpSender__rtp_header_extensions_map
            data=packet.serialize(mapper);decoded=RtpPacket.parse(data,mapper)
            assert decoded.ssrc==packet.ssrc and decoded.sequence_number==packet.sequence_number and decoded.timestamp==packet.timestamp
            assert decoded.payload==packet.payload and decoded.payload_type==packet.payload_type and decoded.marker==packet.marker and decoded.csrc==packet.csrc
            assert decoded.extensions.mid==packet.extensions.mid
            if kind=='video':assert decoded.extensions.abs_send_time==packet.extensions.abs_send_time
            raw=raw_extensions(data);delays=[value for ext_id,value in raw if ext_id==identifier] if identifier is not None else []
            if supported and kind=='video':assert delays==[b'\0\0\0']
            elif identifier not in [extension.id for extension in calls[kind].headerExtensions]:assert not delays
        if supported:
            # A sender must not add the extension when send parameters omit it.
            sender=transceivers['video'].sender
            assert configure_video_playout_delay(answerer,text)==enabled
            parameters=answerer._RTCPeerConnection__localRtp(transceivers['video'])
            parameters.headerExtensions=[extension for extension in parameters.headerExtensions if extension.uri!=PLAYOUT_DELAY_URI]
            await sender.send(parameters)
            packet=RtpPacket(payload_type=102,payload=b'fallback');packet.extensions.mid=transceivers['video'].mid
            assert not any(ext_id==identifier for ext_id,_ in raw_extensions(packet.serialize(sender._RTCRtpSender__rtp_header_extensions_map)))
    finally:
        await offerer.close();await answerer.close()


async def main():
    assert playout_delay_bytes()==b'\0\0\0'
    assert playout_delay_bytes(10,50)==b'\0\x10\x05'
    await scenario()
    await scenario(4)
    await scenario(20)  # Two-byte extension packing preserves existing fields.
    await scenario(4,'recvonly')
    await scenario(4,'sendonly')
    await scenario(4,collision=True)
    print(json.dumps({'offeredExtensionOnly':True,'answerAndSenderParametersMatch':True,'midAndAbsSendTimePreserved':True,'ssrcAndPayloadPreserved':True,'oneAndTwoByteExtensionIds':True,'unsupportedOrConflictingOffersUnchanged':True,'networkTraffic':False}))


asyncio.run(main())
