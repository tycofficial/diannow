"""Prefer direct TURN/TCP, retaining the user's local proxy as fallback."""
import asyncio,socket
from urllib.parse import urlsplit
from aioice import turn

def install_turn_proxy(proxy_url,prefer_direct=True,direct_timeout=3.0):
    if not proxy_url or getattr(turn.create_turn_endpoint,'_desktop_proxy',False):return
    proxy=urlsplit(proxy_url)
    if proxy.scheme!='http' or proxy.hostname not in ['127.0.0.1','localhost','::1']:raise ValueError('Expected the existing local HTTP proxy')
    original=turn.create_turn_endpoint
    async def create(protocol_factory,server_addr,username,password,lifetime=turn.DEFAULT_ALLOCATION_LIFETIME,channel_refresh_time=turn.DEFAULT_CHANNEL_REFRESH_TIME,ssl=None,transport='udp'):
        if transport!='tcp':return await original(protocol_factory,server_addr,username,password,lifetime,channel_refresh_time,ssl,transport)
        if prefer_direct:
            try:
                async with asyncio.timeout(direct_timeout):
                    relay,receiver=await original(protocol_factory,server_addr,username,password,lifetime,channel_refresh_time,ssl,transport)
                relay._desktop_turn_route='direct-tcp'
                return relay,receiver
            except Exception:pass
        return await _proxy_tcp_endpoint(proxy,protocol_factory,server_addr,username,password,lifetime,channel_refresh_time,ssl)
    create._desktop_proxy=True;turn.create_turn_endpoint=create

async def _proxy_tcp_endpoint(proxy,protocol_factory,server_addr,username,password,lifetime,channel_refresh_time,ssl):
        loop=asyncio.get_running_loop();sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM);sock.setblocking(False);inner=None
        try:
            async with asyncio.timeout(5):
                await loop.sock_connect(sock,(proxy.hostname,proxy.port or 80))
                host=server_addr[0]+':'+str(server_addr[1])
                await loop.sock_sendall(sock,('CONNECT '+host+' HTTP/1.1\r\nHost: '+host+'\r\n\r\n').encode('ascii'))
                header=b''
                while b'\r\n\r\n' not in header:
                    data=await loop.sock_recv(sock,1024)
                    if not data or len(header)>8192:raise ConnectionError('Local proxy tunnel closed')
                    header+=data
                if b' 200 ' not in header.split(b'\r\n',1)[0]:raise ConnectionError('Local proxy rejected TURN tunnel')
                inner,protocol=await loop.create_connection(lambda:turn.TurnClientTcpProtocol(server_addr,username,password,lifetime,channel_refresh_time),sock=sock,ssl=ssl,server_hostname=server_addr[0] if ssl else None)
                sock=None
            relay=turn.TurnTransport(protocol);receiver=protocol_factory();await relay._connect(receiver)
            relay._desktop_turn_route='proxy-tcp'
            return relay,receiver
        except BaseException:
            if inner:inner.close()
            if sock:sock.close()
            raise
