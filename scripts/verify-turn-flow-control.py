"""A blocked TURN video must yield to audio and observe close/cancellation."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch
from turn_flow_control import install_turn_flow_control
from rtp_scheduling import PacketPacer
class Writer:
    def __init__(self):self.queued=16000
    def get_write_buffer_size(self):return self.queued
    def get_extra_info(self,key):return None


async def verify_repair_budget():
    """Exercise the installed wrapper, real pacer and repair accounting."""
    now=[100.0];sent=[];fail=[False]
    async def sleep(delay):now[0]+=delay
    async def send(data):
        if fail[0]:raise OSError('Simulated transport send failure')
        sent.append(data)
    clock=SimpleNamespace(monotonic=lambda:now[0],perf_counter=lambda:now[0])
    pair=SimpleNamespace(protocol=SimpleNamespace(transport=object()),local_candidate=SimpleNamespace(type='host'),remote_candidate=SimpleNamespace(type='relay'))
    connection=SimpleNamespace(_nominated={1:pair},_closed=False,turn_transport='udp')
    dtls=SimpleNamespace(transport=SimpleNamespace(_connection=connection),state='connected',_send_rtp=send)
    delivery=SimpleNamespace(last_feedback=None,last_buffer=600,buffer_growth_streak=0)
    encoder=SimpleNamespace(fixed_profile=True,profile_bitrate=8_000_000,delivery=delivery)
    sender=SimpleNamespace(_RTCRtpSender__rtp_task=asyncio.current_task(),_video_retransmit_task=None,_RTCRtpSender__rtt=.6,_RTCRtpSender__encoder=encoder)
    video=bytes([128,102])+bytes(1198);rtx=bytes([128,103])+bytes(1198);audio=bytes([128,111])+bytes(100)
    wire=len(rtx)+64
    async def repair(data=rtx):
        # Mark this actual task as the owned repair worker, as production does.
        sender._video_retransmit_task=asyncio.current_task()
        sender._RTCRtpSender__rtp_task=None
        try:await dtls._send_rtp(data)
        finally:
            sender._video_retransmit_task=None
            sender._RTCRtpSender__rtp_task=asyncio.current_task()
    with patch('turn_flow_control.time',clock),patch('turn_flow_control.PacketPacer',lambda:PacketPacer(clock=lambda:now[0],sleep=sleep)):
        state=install_turn_flow_control(dtls,[102,103],video_sender=sender)
        await dtls._send_rtp(video)
        await repair()
        await dtls._send_rtp(video)
        assert state['repairWireBytes']==wire
        assert state['pacingBitsPerSecond']==9_200_000 and state['repairBlockReason']=='no-feedback'
        delivery.last_feedback=now[0]
        await dtls._send_rtp(video)
        assert state['pacingBitsPerSecond']==9_200_000+wire*8
        assert state['repairBlockReason']=='none' and not state['repairHeadroomBlocked'], 'Constant 600ms receiver buffer is not queue growth'
        before=state['repairWireBytes'];observed=state['repairWireBitsPerSecond'];primary=state['primaryWireBytes']
        # Neither successful audio on the repair task nor failed RTX sends
        # are repair traffic that the network must reserve future headroom for.
        await repair(audio)
        assert state['repairWireBytes']==before and state['primaryWireBytes']==primary
        fail[0]=True
        try:await repair()
        except OSError:pass
        else:raise AssertionError('Transport failure was swallowed')
        fail[0]=False
        await dtls._send_rtp(video)
        assert state['repairWireBytes']==before and state['repairWireBitsPerSecond']==observed
        # A 50% RTT increase withdraws the measured allowance immediately.
        sender._RTCRtpSender__rtt=.9
        await dtls._send_rtp(video)
        assert state['pacingBitsPerSecond']==9_200_000 and state['repairBlockReason']=='rtt-growth'
        sender._RTCRtpSender__rtt=.6
        now[0]+=9
        delivery.last_feedback=now[0]
        await repair();await dtls._send_rtp(video)
        assert state['pacingBitsPerSecond']==9_200_000 and state['repairBlockReason']=='congestion-hold'
        now[0]+=1.1
        delivery.last_feedback=now[0]
        await repair();await dtls._send_rtp(video)
        assert state['pacingBitsPerSecond']>9_200_000 and state['repairBlockReason']=='none', 'Do not compound two congestion holds'
        # Fresh repair demand cannot override stale receiver observations.
        delivery.last_feedback=now[0]-6
        await repair();await dtls._send_rtp(video)
        assert state['pacingBitsPerSecond']==9_200_000 and state['repairBlockReason']=='no-feedback'
        delivery.last_feedback=now[0]
        delivery.buffer_growth_streak=2
        await dtls._send_rtp(video)
        assert state['pacingBitsPerSecond']==9_200_000 and state['repairBlockReason']=='buffer-growth'
        assert encoder.profile_bitrate==8_000_000 and encoder.fixed_profile


async def main():
    writer=Writer();turn=SimpleNamespace(_TurnTransport__inner_protocol=SimpleNamespace(transport=writer))
    connection=SimpleNamespace(_nominated={1:SimpleNamespace(protocol=SimpleNamespace(transport=turn))},_closed=False)
    sent=[]
    async def send(data):sent.append(data)
    dtls=SimpleNamespace(transport=SimpleNamespace(_connection=connection),state='connected',_send_rtp=send)
    sender=SimpleNamespace(_RTCRtpSender__rtp_task=None)
    state=install_turn_flow_control(dtls,[102],video_sender=sender);assert install_turn_flow_control(dtls,[103]) is state
    video=asyncio.create_task(dtls._send_rtp(bytes([128,102])));sender._RTCRtpSender__rtp_task=video;await asyncio.sleep(.015)
    assert not video.done() and not sent
    await asyncio.wait_for(dtls._send_rtp(bytes([128,102])),.02)
    assert state['skippedStaleRetransmits']==1,'NACK retransmission must never block the DTLS receive loop'
    await dtls._send_rtp(bytes([128,111]));assert len(sent)==1,'Audio must pass while video waits'
    writer.queued=1000;await asyncio.wait_for(video,.1);assert sent[-1]==bytes([128,102])
    writer.queued=16000;video=asyncio.create_task(dtls._send_rtp(bytes([128,103])));sender._RTCRtpSender__rtp_task=video;await asyncio.sleep(.01);dtls.state='closed'
    try:await video
    except ConnectionError:pass
    else:raise AssertionError('Closed transport must interrupt a waiting video')
    assert state['waits']>0 and state['maxQueuedBytes']==16000
    # A pending Windows UDP datagram is not a TCP stream backlog. Its TURN
    # path still uses the packet pacer, with no arbitrary 8KB/5ms gate.
    udp_writer=Writer()
    udp_pair=SimpleNamespace(protocol=SimpleNamespace(transport=SimpleNamespace(_TurnTransport__inner_protocol=SimpleNamespace(transport=udp_writer))),local_candidate=SimpleNamespace(type='relay'),remote_candidate=SimpleNamespace(type='relay'))
    udp_connection=SimpleNamespace(_nominated={1:udp_pair},_closed=False,turn_transport='udp')
    udp_dtls=SimpleNamespace(transport=SimpleNamespace(_connection=udp_connection),state='connected',_send_rtp=send)
    udp_sender=SimpleNamespace(_RTCRtpSender__rtp_task=asyncio.current_task(),_RTCRtpSender__encoder=SimpleNamespace(fixed_profile=True,profile_bitrate=8000000))
    udp=install_turn_flow_control(udp_dtls,[102],video_sender=udp_sender)
    for _ in range(8):await asyncio.wait_for(udp_dtls._send_rtp(bytes([128,102])+bytes(1198)),.05)
    assert udp['waits']==udp['maxQueuedBytes']==udp['skippedStaleRetransmits']==0
    assert udp['relayed'] and not udp['configuredWriters']
    # The desktop can use a UDP host/srflx socket while only the phone's
    # incoming leg uses TURN/TCP. Its queue is invisible to our socket.
    pair=SimpleNamespace(protocol=SimpleNamespace(transport=object()),local_candidate=SimpleNamespace(type='host'),remote_candidate=SimpleNamespace(type='relay'))
    connection=SimpleNamespace(_nominated={1:pair},_closed=False)
    dtls=SimpleNamespace(transport=SimpleNamespace(_connection=connection),state='connected',_send_rtp=send)
    sender=SimpleNamespace(_RTCRtpSender__rtp_task=asyncio.current_task(),_RTCRtpSender__rtt=.6,_RTCRtpSender__encoder=SimpleNamespace(requested_bitrate=350000))
    remote=install_turn_flow_control(dtls,[102],video_sender=sender);started=asyncio.get_running_loop().time()
    for _ in range(16):await dtls._send_rtp(bytes([128,102])+bytes(1198))
    elapsed=asyncio.get_running_loop().time()-started
    assert elapsed>.08 and remote['relayed'] and not remote['configuredWriters'],'Remote-only relay traffic bypassed pacing'
    assert remote['pacingBitsPerSecond']==900000,'A static-screen estimate alone must not permanently cap throughput'
    sender._RTCRtpSender__rtt=.9;started=asyncio.get_running_loop().time()
    for _ in range(16):await dtls._send_rtp(bytes([128,102])+bytes(1198))
    assert asyncio.get_running_loop().time()-started>.3
    assert remote['pacingBitsPerSecond']==385000,'Confirmed RTT queue growth must restore the conservative bandwidth cap'
    sender._RTCRtpSender__rtt=.6
    await dtls._send_rtp(bytes([128,102]))
    assert remote['pacingBitsPerSecond']==385000,'A brief recovery must not immediately refill the queue'
    sender._RTCRtpSender__encoder.fixed_profile=True
    sender._RTCRtpSender__encoder.profile_bitrate=8000000
    await dtls._send_rtp(bytes([128,102]))
    assert remote['pacingBitsPerSecond']==9200000,'Fixed 1080P must retain its selected bitrate plus packet overhead despite noisy REMB'
    await verify_repair_budget()
    print('Verified TCP backlog bounds, UDP pacing without TCP gates, audio progress, actual-successful-RTX accounting, fresh feedback, fixed receiver delay, congestion withdrawal / hold, fixed media budget and prompt close. No network traffic.')
asyncio.run(main())
