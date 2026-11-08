"""Compare bound TURN bytes, task creation and CPU work without a network."""
import asyncio
import json
import struct
import time
from types import SimpleNamespace
from aioice.turn import TurnTransport
from turn_fast_path import install_bound_channel_send

PEER=('192.0.2.1',5000)

class Inner:
    def __init__(self):
        self.peer_to_channel={PEER:0x4000};self.channel_refresh_at={0x4000:200}
        self.peer_connect_waiters={};self.sent=[];self.errors=[]
        self.receiver=SimpleNamespace(error_received=self.errors.append)
    def _send(self,data):self.sent.append(data)
    async def send_data(self,data,addr):self._send(b'fallback'+data)

async def main():
    inner=Inner();transport=TurnTransport(inner);now=[100.0]
    state=install_bound_channel_send(transport,clock=lambda:now[0])
    assert install_bound_channel_send(transport) is state
    packet=b'original-encrypted-datagram'
    transport.sendto(packet,PEER)
    assert inner.sent==[struct.pack('!HH',0x4000,len(packet))+packet]
    assert not transport._TurnTransport__send_tasks
    for reason in ('unbound','refresh','pending'):
        if reason=='unbound':inner.peer_to_channel.clear()
        elif reason=='refresh':inner.peer_to_channel[PEER]=0x4000;now[0]=201
        else:now[0]=100;inner.peer_connect_waiters[PEER]=[]
        transport.sendto(packet,PEER);await asyncio.sleep(0);await asyncio.sleep(0)
        assert inner.sent[-1]==b'fallback'+packet
    assert state['fallbackPackets']==3
    inner.peer_connect_waiters.clear()
    def broken(data):raise OSError('simulated socket loss')
    inner._send=broken;transport.sendto(packet,PEER)
    assert len(inner.errors)==state['sendErrors']==1
    transport._TurnTransport__close_task=object();before=dict(state);transport.sendto(packet,PEER)
    assert state==before,'Never write to a closing TURN allocation'
    assert install_bound_channel_send(SimpleNamespace()) is None

    results=[]
    for fast in (False,True):
        inner=Inner();inner.channel_refresh_at[0x4000]=time.time()+300
        count=[0]
        def record(data):count[0]+=1
        inner._send=record
        async def original(data,addr):inner._send(struct.pack('!HH',0x4000,len(data))+data)
        inner.send_data=original;transport=TurnTransport(inner)
        if fast:install_bound_channel_send(transport)
        tasks=[0];loop=asyncio.get_running_loop();old_factory=loop.get_task_factory()
        def factory(loop,coro,context=None):
            tasks[0]+=1
            return asyncio.Task(coro,loop=loop,context=context)
        loop.set_task_factory(factory);start=time.process_time()
        try:
            for index in range(20000):
                transport.sendto(bytes(1200),PEER)
                if index%64==63:await asyncio.sleep(0)
            await asyncio.sleep(0);await asyncio.sleep(0)
        finally:loop.set_task_factory(old_factory)
        results.append({'fast':fast,'datagrams':count[0],'tasksCreated':tasks[0],'cpuMs':round(1000*(time.process_time()-start),2)})
        assert count[0]==20000 and tasks[0]==(0 if fast else 20000)
    print(json.dumps({'boundTurnBenchmark':results,'wireBytesIdentical':True,'fallbackAndClosePreserved':True,'networkTraffic':False}))

asyncio.run(main())
