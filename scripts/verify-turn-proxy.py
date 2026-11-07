"""Direct TCP preference and local-proxy fallback without network IO."""
import asyncio
from types import SimpleNamespace
from aioice import turn
import turn_proxy

async def main():
    original=turn.create_turn_endpoint
    original_proxy=turn_proxy._proxy_tcp_endpoint
    calls=[]
    mode=['success']
    async def direct(*args,**kwargs):
        calls.append(('direct',args[-1] if len(args)>7 else kwargs.get('transport')))
        if mode[0]=='fail':raise OSError('mock blocked direct route')
        if mode[0]=='timeout':await asyncio.Future()
        if mode[0]=='cancel':raise asyncio.CancelledError
        return SimpleNamespace(),object()
    async def proxy(*args):
        calls.append(('proxy',args[0].hostname))
        return SimpleNamespace(),object()
    try:
        turn.create_turn_endpoint=direct;turn_proxy._proxy_tcp_endpoint=proxy
        turn_proxy.install_turn_proxy('http://127.0.0.1:7890',direct_timeout=.01)
        wrapped=turn.create_turn_endpoint
        relay,_=await wrapped(lambda:None,('relay.example',3478),'test','test',transport='tcp')
        assert relay._desktop_turn_route=='direct-tcp' and calls==[('direct','tcp')]
        mode[0]='fail';await wrapped(lambda:None,('relay.example',3478),'test','test',transport='tcp')
        assert calls[-2:]==[('direct','tcp'),('proxy','127.0.0.1')]
        mode[0]='timeout';await wrapped(lambda:None,('relay.example',3478),'test','test',transport='tcp')
        assert calls[-2:]==[('direct','tcp'),('proxy','127.0.0.1')]
        mode[0]='cancel';before=len(calls)
        try:await wrapped(lambda:None,('relay.example',3478),'test','test',transport='tcp')
        except asyncio.CancelledError:pass
        else:raise AssertionError('Caller cancellation must propagate')
        assert len(calls)==before+1 and calls[-1][0]=='direct'
        mode[0]='success';await wrapped(lambda:None,('relay.example',3478),'test','test',transport='udp')
        assert calls[-1]==('direct','udp')
        turn_proxy.install_turn_proxy('http://127.0.0.1:7890');assert turn.create_turn_endpoint is wrapped
        turn.create_turn_endpoint=direct
        turn_proxy.install_turn_proxy('http://127.0.0.1:7890',prefer_direct=False)
        before=len(calls);await turn.create_turn_endpoint(lambda:None,('relay.example',3478),'test','test',transport='tcp')
        assert calls[before:]==[('proxy','127.0.0.1')]
    finally:
        turn.create_turn_endpoint=original;turn_proxy._proxy_tcp_endpoint=original_proxy
    print('Verified direct TCP first, bounded timeout/error fallback to existing local proxy, UDP unchanged, idempotence and cancellation. No network IO.')

asyncio.run(main())
