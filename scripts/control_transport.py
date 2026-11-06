"""Negotiate data on its own ICE/DTLS transport when the offer permits it."""
from aiortc import RTCSessionDescription,sdp

def isolate_control(description,enabled=True):
    if not enabled:return description,False
    parsed=sdp.SessionDescription.parse(description.sdp)
    applications=[media for media in parsed.media if media.kind=='application']
    media=[media for media in parsed.media if media.kind in ['audio','video']]
    if len(applications)!=1 or not media:return description,False
    application=applications[0]
    # max-bundle / bundle-only offers do not have an independent data port.
    if application.port==0:return description,False
    control_mid=application.rtp.muxId
    changed=False
    for group in parsed.group:
        if group.semantic=='BUNDLE' and control_mid in group.items and len(group.items)>1:
            group.items=[mid for mid in group.items if mid!=control_mid];changed=True
    return (RTCSessionDescription(sdp=str(parsed),type=description.type),True) if changed else (description,False)
