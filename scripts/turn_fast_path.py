"""Send on a live TURN channel without scheduling one task per datagram.

Only already-bound, unexpired channels use the fast path. Authentication,
initial binding, refresh, TCP padding and allocation cleanup stay in aioice.
The patch belongs to one allocation and never changes a library class.
"""
import struct
import time


def install_bound_channel_send(transport, clock=time.time):
    existing=getattr(transport,'_desktop_turn_fast_path',None)
    if existing is not None:return existing
    inner=getattr(transport,'_TurnTransport__inner_protocol',None)
    fields=('peer_to_channel','peer_connect_waiters','channel_refresh_at','_send')
    if inner is None or not all(hasattr(inner,name) for name in fields):return None
    original=transport.sendto
    state={'boundPackets':0,'fallbackPackets':0,'sendErrors':0}
    def sendto(data,addr):
        if getattr(transport,'_TurnTransport__close_task',None) is not None:return
        channel=inner.peer_to_channel.get(addr)
        if channel is None or addr in inner.peer_connect_waiters or clock()>inner.channel_refresh_at.get(channel,0):
            state['fallbackPackets']+=1
            return original(data,addr)
        try:
            inner._send(struct.pack('!HH',channel,len(data))+data)
            state['boundPackets']+=1
        except Exception as error:
            # Match TurnTransport._send_done: a socket error belongs to the
            # receiving protocol, not the RTP task calling this synchronous API.
            state['sendErrors']+=1
            if inner.receiver is not None:inner.receiver.error_received(error)
    transport.sendto=sendto
    transport._desktop_turn_fast_path=state
    return state


def install_turn_fast_path(peer):
    connections={item.receiver.transport.transport._connection for item in peer.getTransceivers()}
    if peer.sctp:connections.add(peer.sctp.transport.transport._connection)
    return [state for connection in connections for protocol in connection._protocols
            if (state:=install_bound_channel_send(protocol.transport)) is not None]
