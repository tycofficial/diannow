"""No-network, deterministic checks for cooperative RTP sending."""
import asyncio
from rtp_scheduling import install_cooperative_rtp_send


class Transport:
    def __init__(self):self.packets=[];self.fail=None
    async def _send_rtp(self,data):
        if data is self.fail:raise RuntimeError('transport failure')
        self.packets.append(data)


async def verify():
    # Every await in the transport is synchronous. Other tasks therefore only
    # run when the installed wrapper explicitly yields.
    transport=Transport();state=install_cooperative_rtp_send(transport,clock=lambda:0)
    original_wrapper=transport._send_rtp
    assert install_cooperative_rtp_send(transport) is state
    assert transport._send_rtp is original_wrapper
    packets=[bytes([i%256])*1234 for i in range(257)];events=[];done=False
    async def observer(name):
        await asyncio.sleep(0)
        events.append((name,len(transport.packets),done))
    observers=[asyncio.create_task(observer('audio')),asyncio.create_task(observer('input'))]
    for packet in packets:await transport._send_rtp(packet)
    done=True;await asyncio.gather(*observers)
    assert transport.packets==packets
    assert {name for name,_,_ in events}=={'audio','input'}
    assert all(0<count<len(packets) and not completed for _,count,completed in events),events
    assert state['cooperativeYields']==16 and state['maxBurstPackets']==16

    # Sparse packets, including RTCP, incur no positive sleep or extra yield.
    now=[0.0];yields=[]
    async def fake_yield():yields.append(now[0])
    sparse=Transport();install_cooperative_rtp_send(sparse,clock=lambda:now[0],yield_now=fake_yield)
    rtcp=b'\x80\xc8\x00\x01control';payloads=[b'video',b'audio',rtcp]
    for payload in payloads:
        await sparse._send_rtp(payload);now[0]+=.010;await asyncio.sleep(0)
    assert sparse.packets==payloads and not yields

    # The time budget also yields when a few expensive sends consume 2 ms.
    timed=Transport();original_send=timed._send_rtp
    async def slow_cpu_send(data):
        await original_send(data);now[0]+=.0011
    timed._send_rtp=slow_cpu_send;now[0]=0;yields.clear()
    diagnostics=install_cooperative_rtp_send(timed,clock=lambda:now[0],yield_now=fake_yield)
    await timed._send_rtp(b'1');assert not yields
    await timed._send_rtp(b'2');assert len(yields)==1
    assert diagnostics['maxBurstPackets']==2 and diagnostics['maxBurstMs']>=2

    # Keep transport failures visible to aiortc and never count failed sends.
    failing=Transport();bad=object();failing.fail=bad
    failed_state=install_cooperative_rtp_send(failing,clock=lambda:0)
    try:await failing._send_rtp(bad)
    except RuntimeError as error:assert str(error)=='transport failure'
    else:raise AssertionError('Transport errors must propagate')
    assert failed_state['sentPackets']==0
    print('Verified RTP payload/order preservation, BUNDLE idempotence, audio/input fairness, sparse RTCP, 2 ms burst budget and transport errors. No network or positive sleep was used.')


asyncio.run(verify())
