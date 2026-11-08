"""Bound TURN/TCP video backlog while leaving control and audio responsive."""
import asyncio,socket,time,json
from rtp_scheduling import PacketPacer
from repair_headroom import RepairHeadroom

def install_turn_flow_control(dtls,video_payload_types,high_water=8192,video_sender=None):
    existing=getattr(dtls,'_turn_video_flow',None)
    if existing is not None:
        existing['videoPayloadTypes'].update(video_payload_types)
        if video_sender is not None:existing['videoSenders'].append(video_sender)
        return existing
    state={'videoPayloadTypes':set(video_payload_types),'videoSenders':[video_sender] if video_sender is not None else [],'waits':0,'skippedStaleRetransmits':0,'maxQueuedBytes':0,'configuredWriters':set(),'lastLog':0.0,'nextPacketAt':0.0,'audioEnabled':False,'pacingBitsPerSecond':0,'relayed':False,'minimumRtt':None,'congestedUntil':0.0}
    # This wrapper already keeps congestion active for ten seconds. Do not
    # append a second ten-second hold after that window in the repair policy.
    dtls._turn_video_flow=state;original=dtls._send_rtp;pacer=PacketPacer();headroom=RepairHeadroom(clock=time.monotonic,hold_seconds=0)
    state.update(primaryWireBytes=0,repairWireBytes=0,repairBlockReason='no-feedback')
    def queue_size():
        queued=0
        for pair in dtls.transport._connection._nominated.values():
            state['relayed']=state['relayed'] or any(getattr(getattr(pair,name,None),'type',None)=='relay' for name in ['local_candidate','remote_candidate'])
            inner=getattr(pair.protocol.transport,'_TurnTransport__inner_protocol',None)
            # UDP has no TCP stream backlog or head-of-line queue. On Windows
            # its asynchronous datagram writer may briefly report pending
            # bytes; applying the 8KB TCP gate adds 5ms stalls to paced UDP.
            if getattr(dtls.transport._connection,'turn_transport',None)=='udp':continue
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
        is_video=len(data)>=2 and (data[1]&127) in state['videoPayloadTypes'];repair=False
        if is_video:
            # RTCP NACK handling runs inside the DTLS receive loop. Waiting
            # there blocks incoming keyboard, pointer and SCTP ACK packets.
            # Original video and the owned repair worker may wait. Unexpected
            # callers (including a legacy RTCP handler) must remain nonblocking.
            primary=not state['videoSenders'] or any(asyncio.current_task() is getattr(sender,'_RTCRtpSender__rtp_task',None) for sender in state['videoSenders'])
            repair=any(asyncio.current_task() is getattr(sender,'_video_retransmit_task',None) for sender in state['videoSenders'])
            if not primary and not repair and queue_size()>high_water:
                state['skippedStaleRetransmits']+=1;return
            while queue_size()>high_water:
                if dtls.state in ['closed','failed'] or dtls.transport._connection._closed:raise ConnectionError('TURN transport closed')
                state['waits']+=1;now=time.perf_counter()
                if now-state['lastLog']>5:
                    state['lastLog']=now;print(json.dumps({'turnVideoBackpressure':True,'queuedBytes':queue_size(),'maxQueuedBytes':state['maxQueuedBytes']}),flush=True)
                await asyncio.sleep(.005)
            if (primary or repair) and (state['configuredWriters'] or state['relayed']):
                # The relay's outgoing queue is beyond our local TCP socket.
                # Respect receiver bandwidth feedback as well as local drain.
                encoder=getattr(video_sender,'_RTCRtpSender__encoder',None) if video_sender else None
                fixed=getattr(encoder,'fixed_profile',False)
                estimate=getattr(encoder,'profile_bitrate',8000000) if fixed else getattr(encoder,'requested_bitrate',2500000)
                estimate=min(estimate,getattr(encoder,'profile_bitrate',100000000))
                rtt=getattr(video_sender,'_RTCRtpSender__rtt',None) if video_sender else None
                if isinstance(rtt,(int,float)) and 0<rtt<60:state['minimumRtt']=min(state['minimumRtt'] or rtt,rtt)
                delivery=getattr(encoder,'delivery',None)
                buffered=getattr(delivery,'last_buffer',None)
                feedback_at=getattr(delivery,'last_feedback',None)
                feedback_fresh=isinstance(feedback_at,(int,float)) and 0<=time.monotonic()-feedback_at<=5
                # Loss recovery itself enlarges playout delay on a distant
                # route. Only sustained growth indicates a growing queue for
                # a fixed profile; a constant 600ms playout delay is not a
                # measurement of the link's bandwidth.
                buffer_queue=feedback_fresh and isinstance(buffered,(int,float)) and buffered>=150 and (not fixed or getattr(delivery,'buffer_growth_streak',0)>=2)
                rtt_queue=isinstance(rtt,(int,float)) and (rtt>1.5 or state['minimumRtt'] is not None and rtt-state['minimumRtt']>max(.075,state['minimumRtt']*.25 if fixed else 0))
                queued=rtt_queue or buffer_queue
                now=time.perf_counter()
                if queued:state['congestedUntil']=now+10
                queued=queued or now<state['congestedUntil']
                # Offered traffic on a static screen is not link capacity.
                # Use a usable budget until RTT growth / receiver buffering
                # confirms an actual queue. Audio is already reflected by
                # receiver bandwidth feedback; do not subtract it twice.
                budget=max(96000,min(40000000,estimate*1.15)) if fixed else max(96000,min(8000000,estimate*1.1 if queued else max(900000,estimate*1.1)))
                if fixed:
                    budget=min(40000000,headroom.budget(estimate,budget,congested=queued,feedback_fresh=feedback_fresh))
                    state.update(headroom.state)
                    state['repairBlockReason']='rtt-growth' if rtt_queue else 'buffer-growth' if buffer_queue else 'congestion-hold' if queued else 'no-feedback' if not feedback_fresh else 'none'
                if encoder is not None:encoder.capacity_bitrate=max(350000,round(budget/1.1))
                state['pacingBitsPerSecond']=round(budget)
                await pacer.wait(len(data)+64,budget)
                state['nextPacketAt']=pacer.next_at
        await original(data)
        if is_video:
            wire_bytes=len(data)+64
            state['repairWireBytes' if repair else 'primaryWireBytes']+=wire_bytes
            if repair:headroom.record_repair(wire_bytes)
    dtls._send_rtp=send
    return state
