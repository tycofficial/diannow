"""Bound TURN/TCP video backlog while leaving control and audio responsive."""
import asyncio,socket,time,json

def install_turn_flow_control(dtls,video_payload_types,high_water=8192,video_sender=None):
    existing=getattr(dtls,'_turn_video_flow',None)
    if existing is not None:
        existing['videoPayloadTypes'].update(video_payload_types)
        if video_sender is not None:existing['videoSenders'].append(video_sender)
        return existing
    state={'videoPayloadTypes':set(video_payload_types),'videoSenders':[video_sender] if video_sender is not None else [],'waits':0,'skippedStaleRetransmits':0,'maxQueuedBytes':0,'configuredWriters':set(),'lastLog':0.0,'nextPacketAt':0.0,'audioEnabled':False,'pacingBitsPerSecond':0,'relayed':False,'minimumRtt':None,'congestedUntil':0.0}
    dtls._turn_video_flow=state;original=dtls._send_rtp
    def queue_size():
        queued=0
        for pair in dtls.transport._connection._nominated.values():
            state['relayed']=state['relayed'] or any(getattr(getattr(pair,name,None),'type',None)=='relay' for name in ['local_candidate','remote_candidate'])
            inner=getattr(pair.protocol.transport,'_TurnTransport__inner_protocol',None)
            writer=getattr(inner,'transport',None)
            if writer is None or not hasattr(writer,'get_write_buffer_size'):continue
            if id(writer) not in state['configuredWriters']:
                stream=writer.get_extra_info('socket')
                if stream is not None:
                    try:stream.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,262144);stream.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                    except OSError:pass
                state['configuredWriters'].add(id(writer))
            queued=max(queued,writer.get_write_buffer_size())
        state['maxQueuedBytes']=max(state['maxQueuedBytes'],queued)
        return queued
    async def send(data):
        if len(data)>=2 and (data[1]&127) in state['videoPayloadTypes']:
            # RTCP NACK handling runs inside the DTLS receive loop. Waiting
            # there blocks incoming keyboard, pointer and SCTP ACK packets.
            # Throttle only the primary video task; omit stale retransmits
            # under congestion and let the next keyframe recover the decoder.
            primary=not state['videoSenders'] or any(asyncio.current_task() is getattr(sender,'_RTCRtpSender__rtp_task',None) for sender in state['videoSenders'])
            if not primary and queue_size()>high_water:
                state['skippedStaleRetransmits']+=1;return
            while queue_size()>high_water:
                if dtls.state in ['closed','failed'] or dtls.transport._connection._closed:raise ConnectionError('TURN transport closed')
                state['waits']+=1;now=time.monotonic()
                if now-state['lastLog']>5:
                    state['lastLog']=now;print(json.dumps({'turnVideoBackpressure':True,'queuedBytes':queue_size(),'maxQueuedBytes':state['maxQueuedBytes']}),flush=True)
                await asyncio.sleep(.005)
            if primary and (state['configuredWriters'] or state['relayed']):
                # The relay's outgoing queue is beyond our local TCP socket.
                # Respect receiver bandwidth feedback as well as local drain.
                encoder=getattr(video_sender,'_RTCRtpSender__encoder',None) if video_sender else None
                estimate=getattr(encoder,'requested_bitrate',2500000)
                estimate=min(estimate,getattr(encoder,'profile_bitrate',100000000))
                rtt=getattr(video_sender,'_RTCRtpSender__rtt',None) if video_sender else None
                if isinstance(rtt,(int,float)) and 0<rtt<60:state['minimumRtt']=min(state['minimumRtt'] or rtt,rtt)
                delivery=getattr(encoder,'delivery',None)
                buffered=getattr(delivery,'last_buffer',None)
                queued=(isinstance(rtt,(int,float)) and (rtt>1.5 or state['minimumRtt'] is not None and rtt-state['minimumRtt']>.075)) or (isinstance(buffered,(int,float)) and buffered>=150)
                now=time.monotonic()
                if queued:state['congestedUntil']=now+10
                queued=queued or now<state['congestedUntil']
                # Offered traffic on a static screen is not link capacity.
                # Use a usable budget until RTT growth / receiver buffering
                # confirms an actual queue. Audio is already reflected by
                # receiver bandwidth feedback; do not subtract it twice.
                budget=max(96000,min(8000000,estimate*1.1 if queued else max(900000,estimate*1.1)))
                if encoder is not None:encoder.capacity_bitrate=max(350000,round(budget/1.1))
                state['pacingBitsPerSecond']=round(budget)
                delay=state['nextPacketAt']-time.monotonic()
                if delay>0:await asyncio.sleep(delay)
                state['nextPacketAt']=time.monotonic()+8*(len(data)+64)/budget
        await original(data)
    dtls._send_rtp=send
    return state
