"""A blocked TURN video must yield to audio and observe close/cancellation."""
import asyncio
from types import SimpleNamespace
from turn_flow_control import install_turn_flow_control
class Writer:
    def __init__(self):self.queued=16000
    def get_write_buffer_size(self):return self.queued
    def get_extra_info(self,key):return None
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
    # The desktop can use a UDP host/srflx socket while only the phone's
    # incoming leg uses TURN/TCP. Its queue is invisible to our socket.
    pair=SimpleNamespace(protocol=SimpleNamespace(transport=object()),local_candidate=SimpleNamespace(type='host'),remote_candidate=SimpleNamespace(type='relay'))
    connection=SimpleNamespace(_nominated={1:pair},_closed=False)
    dtls=SimpleNamespace(transport=SimpleNamespace(_connection=connection),state='connected',_send_rtp=send)
    sender=SimpleNamespace(_RTCRtpSender__rtp_task=asyncio.current_task(),_RTCRtpSender__encoder=SimpleNamespace(requested_bitrate=350000))
    remote=install_turn_flow_control(dtls,[102],video_sender=sender);started=asyncio.get_running_loop().time()
    for _ in range(16):await dtls._send_rtp(bytes([128,102])+bytes(1198))
    elapsed=asyncio.get_running_loop().time()-started
    assert elapsed>.3 and remote['relayed'] and not remote['configuredWriters'],'Remote-only relay traffic bypassed pacing'
    assert remote['pacingBitsPerSecond']==385000,'Low receiver bandwidth must not be replaced by a 1.5Mbps floor'
    print('Verified bounded video queue, audio progress, merged video payload types and prompt close. No network traffic.')
asyncio.run(main())
