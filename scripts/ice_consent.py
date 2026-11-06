"""Allow long-distance TURN replies without disabling ICE consent checks.

aioice 0.12 uses the fixed 500ms STUN RTO for non-retransmitted consent
requests. A valid answer over a long-distance relay can arrive after that
transaction has already been discarded. Scope the response window to this
peer and retain a 30-second limit since the last successful consent reply.
"""
import asyncio,time,json
from aioice import stun

def install_consent_window(pc,response_seconds=2.0,expiry_seconds=30.0):
    connections={item.receiver.transport.transport._connection for item in pc.getTransceivers()}
    if pc.sctp:connections.add(pc.sctp.transport.transport._connection)
    results=[]
    for connection in connections:
        installed=getattr(connection,'_remote_consent_window',None)
        if installed is not None:results.append(installed);continue
        state={'responseSeconds':response_seconds,'expirySeconds':expiry_seconds,'replies':0,'failures':0,'maxRttMs':0.0,'lastSuccess':None}
        connection._remote_consent_window=state;results.append(state)
        for protocol in connection._protocols:
            original=protocol.request
            async def request(message,addr,integrity_key=None,retransmissions=None,*,_protocol=protocol,_original=original,_state=state):
                if retransmissions!=0:return await _original(message,addr,integrity_key=integrity_key,retransmissions=retransmissions)
                if integrity_key is not None:message.add_message_integrity(integrity_key)
                transaction=stun.Transaction(message,addr,_protocol,retransmissions=0)
                if not hasattr(transaction,'_Transaction__timeout_delay'):raise RuntimeError('Unsupported aioice STUN transaction layout')
                transaction._Transaction__timeout_delay=response_seconds
                _protocol.transactions[message.transaction_id]=transaction;started=time.monotonic()
                try:
                    result=await transaction.run();_state['lastSuccess']=time.monotonic();_state['replies']+=1
                    _state['maxRttMs']=round(max(_state['maxRttMs'],1000*(time.monotonic()-started)),1)
                    if _state['replies']==1:print(json.dumps({'iceConsentReply':True,'rttMs':_state['maxRttMs']}),flush=True)
                    return result
                except stun.TransactionError as error:
                    _state['failures']+=1
                    print(json.dumps({'iceConsentError':type(error).__name__,'message':str(error)}),flush=True);raise
                finally:_protocol.transactions.pop(message.transaction_id,None)
            protocol.request=request
        original_query=connection.query_consent
        async def query(*,_connection=connection,_state=state,_original=original_query):
            _state['lastSuccess']=time.monotonic()
            async def guard():
                while not _connection._closed:
                    await asyncio.sleep(.5)
                    if time.monotonic()-_state['lastSuccess']>=expiry_seconds:
                        print(json.dumps({'iceConsentExpired':True,'seconds':expiry_seconds}),flush=True)
                        asyncio.create_task(_connection.close());return
            monitor=asyncio.create_task(guard())
            try:await _original()
            finally:monitor.cancel();await asyncio.gather(monitor,return_exceptions=True)
        connection.query_consent=query
    return results
