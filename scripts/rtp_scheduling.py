"""Cooperative scheduling for one connection's RTP transport.

aiortc's UDP send coroutine can complete without suspending. Yield between
small packet batches so a large video frame cannot starve audio and input.
This does not pace packets or change their payload, order or bitrate.
"""
import asyncio
import time


def install_cooperative_rtp_send(transport,packet_limit=16,burst_seconds=.002,*,clock=None,yield_now=None):
    """Wrap this DTLS transport once and return its mutable diagnostics.

    Install before its RTP senders start. BUNDLE senders sharing this transport
    reuse the first wrapper. The optional clock/yield functions support fully
    deterministic verification without a network or timer delay.
    """
    existing=getattr(transport,'_cooperative_rtp_send',None)
    if existing is not None:return existing
    if not isinstance(packet_limit,int) or packet_limit<1:raise ValueError('packet_limit must be a positive integer')
    if burst_seconds<=0:raise ValueError('burst_seconds must be positive')
    clock=clock or time.perf_counter
    if yield_now is None:
        async def yield_now():await asyncio.sleep(0)
    original_send=transport._send_rtp
    state={'packetLimit':packet_limit,'burstMs':burst_seconds*1000,'sentPackets':0,'cooperativeYields':0,'maxBurstPackets':0,'maxBurstMs':0.0}
    packet_count=0;burst_started=None;reset_handle=None

    def reset_burst():
        nonlocal packet_count,burst_started,reset_handle
        packet_count=0;burst_started=None;reset_handle=None

    async def send(data):
        nonlocal packet_count,burst_started,reset_handle
        now=clock()
        # Reset only when the event loop actually gets a turn. A wall-clock
        # gap can also mean expensive synchronous packet serialization.
        if burst_started is None:
            packet_count=0;burst_started=now
            reset_handle=asyncio.get_running_loop().call_soon(reset_burst)
        await original_send(data)
        finished=clock();packet_count+=1;state['sentPackets']+=1
        # A naturally suspending transport already gave other tasks a turn.
        if burst_started is None:return
        duration=max(0,finished-burst_started)
        state['maxBurstPackets']=max(state['maxBurstPackets'],packet_count)
        state['maxBurstMs']=max(state['maxBurstMs'],duration*1000)
        if packet_count>=packet_limit or duration>=burst_seconds:
            # Reset before yielding: another sender may enter this wrapper
            # while the current sender gives the event loop its turn.
            if reset_handle is not None:reset_handle.cancel()
            reset_burst();state['cooperativeYields']+=1
            await yield_now()

    transport._send_rtp=send
    transport._cooperative_rtp_send=state
    return state
