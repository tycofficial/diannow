"""Opt in to the receiver-offered WebRTC playout-delay RTP extension.

Call configure_video_playout_delay after setRemoteDescription(offer), before
createAnswer / setLocalDescription. No global aiortc capabilities are changed.
The receiver remains responsible for its actual buffering and presentation.
"""
from aiortc import sdp
from aiortc.rtcrtpparameters import RTCRtpHeaderExtensionParameters
from aiortc.rtp import pack_header_extensions,unpack_header_extensions


PLAYOUT_DELAY_URI='http://www.webrtc.org/experiments/rtp-hdrext/playout-delay'


def playout_delay_bytes(minimum_ms=0,maximum_ms=0):
    """Encode two 12-bit delays in the extension's 10 ms units."""
    if not (0<=minimum_ms<=maximum_ms<=40950):raise ValueError('Invalid playout-delay range.')
    minimum=int(minimum_ms//10);maximum=int(maximum_ms//10)
    return ((minimum<<12)|maximum).to_bytes(3,'big')


def _video_offers(offer_sdp):
    # aiortc's SDP parser drops extmap direction, so retain it explicitly.
    sections=[];current=None
    for raw in offer_sdp.splitlines():
        line=raw.strip()
        if line.startswith('m='):
            current={'kind':line[2:].split()[0],'mid':None,'extensions':[]}
            sections.append(current)
        elif current is not None and line.startswith('a=mid:'):current['mid']=line[6:]
        elif current is not None and line.startswith('a=extmap:'):
            fields=line[9:].split()
            if len(fields)<2:continue
            identifier,_,direction=fields[0].partition('/')
            try:identifier=int(identifier)
            except ValueError:continue
            current['extensions'].append((identifier,fields[1],direction))
    return {section['mid']:section for section in sections if section['kind']=='video'}


def _wrap_sender(sender,identifier,value):
    original_send=sender.send
    extension_map=getattr(sender,'_RTCRtpSender__rtp_header_extensions_map',None)
    if extension_map is None:return False
    installed=getattr(sender,'_low_latency_playout_delay',None)
    if installed is not None:return installed==(identifier,value)
    original_set=extension_map.set
    activated=False;enabled=False

    async def send(parameters):
        nonlocal activated,enabled
        enabled=any(extension.id==identifier and extension.uri==PLAYOUT_DELAY_URI for extension in parameters.headerExtensions)
        if enabled and not activated:
            def set_with_playout_delay(values):
                profile,raw=original_set(values)
                if not enabled:return profile,raw
                extensions=unpack_header_extensions(profile,raw)
                # Keep every existing negotiated extension intact.
                if not any(extension_id==identifier for extension_id,_ in extensions):extensions.append((identifier,value))
                return pack_header_extensions(extensions)
            extension_map.set=set_with_playout_delay
            activated=True
        await original_send(parameters)

    sender.send=send
    sender._low_latency_playout_delay=(identifier,value)
    return True


def configure_video_playout_delay(pc,offer_sdp,minimum_ms=0,maximum_ms=0):
    """Return enabled video mids; unsupported offers leave the sender untouched."""
    encoded=playout_delay_bytes(minimum_ms,maximum_ms)
    parsed=sdp.SessionDescription.parse(offer_sdp)
    raw_sections=_video_offers(offer_sdp)
    enabled=[]
    for media in parsed.media:
        if media.kind!='video' or media.port==0 or media.direction not in [None,'recvonly','sendrecv']:continue
        raw=raw_sections.get(media.rtp.muxId)
        if not raw:continue
        matches=[entry for entry in raw['extensions'] if entry[1]==PLAYOUT_DELAY_URI and entry[2] in ['','recvonly','sendrecv'] and 0<entry[0]<256]
        if len(matches)!=1:continue
        identifier=matches[0][0]
        if any(entry[0]==identifier and entry[1]!=PLAYOUT_DELAY_URI for entry in raw['extensions']):continue
        transceiver=next((item for item in pc.getTransceivers() if item.kind=='video' and item.mid==media.rtp.muxId),None)
        if transceiver is None:continue
        if any(extension.id==identifier and extension.uri!=PLAYOUT_DELAY_URI for extension in transceiver._headerExtensions):continue
        if not _wrap_sender(transceiver.sender,identifier,encoded):continue
        if not any(extension.uri==PLAYOUT_DELAY_URI for extension in transceiver._headerExtensions):
            transceiver._headerExtensions.append(RTCRtpHeaderExtensionParameters(id=identifier,uri=PLAYOUT_DELAY_URI))
        enabled.append({'mid':media.rtp.muxId,'extensionId':identifier,'minimumMs':minimum_ms,'maximumMs':maximum_ms})
    return enabled
