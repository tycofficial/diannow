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
    print('Verified bounded video queue, audio progress, merged video payload types and prompt close. No network traffic.')
asyncio.run(main())
