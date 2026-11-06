"""A valid 650ms relay reply must survive; no-consent links still expire."""
import asyncio,time
from types import SimpleNamespace
from aioice import stun
from ice_consent import install_consent_window
class Protocol:
    def __init__(self):self.transactions={};self.regular_calls=0
    async def request(self,*args,**kwargs):self.regular_calls+=1;return 'unchanged'
    def send_stun(self,request,addr):
        transaction=self.transactions[request.transaction_id]
        reply=stun.Message(message_method=stun.Method.BINDING,message_class=stun.Class.RESPONSE,transaction_id=request.transaction_id)
        asyncio.get_running_loop().call_later(.65,transaction.response_received,reply,addr)
class Connection:
    def __init__(self):self._protocols=[Protocol()];self._closed=False;self.closed=asyncio.Event()
    async def query_consent(self):await self.closed.wait()
    async def close(self):self._closed=True;self.closed.set()
def peer(connection):
    ice=SimpleNamespace(_connection=connection);dtls=SimpleNamespace(transport=ice)
    return SimpleNamespace(getTransceivers=lambda:[SimpleNamespace(receiver=SimpleNamespace(transport=dtls))],sctp=None)
async def main():
    connection=Connection();pc=peer(connection)
    state=install_consent_window(pc,response_seconds=.9,expiry_seconds=1.1)[0]
    assert install_consent_window(pc)[0] is state
    running=asyncio.create_task(connection.query_consent());await asyncio.sleep(0)
    protocol=connection._protocols[0];request=stun.Message(message_method=stun.Method.BINDING,message_class=stun.Class.REQUEST)
    await protocol.request(request,('127.0.0.1',9),integrity_key=b'test-only',retransmissions=0)
    assert state['replies']==1 and state['maxRttMs']>=600 and not protocol.transactions
    assert not connection._closed and state['lastSuccess'] is not None
    assert await protocol.request(request,('127.0.0.1',9))=='unchanged' and protocol.regular_calls==1
    await connection.close();await running
    silent=Connection();install_consent_window(peer(silent),response_seconds=.6,expiry_seconds=.8)
    running=asyncio.create_task(silent.query_consent())
    await asyncio.wait_for(silent.closed.wait(),1.5);await running
    assert silent._closed,'Missing consent must still close the link'
    print('Verified 650ms authenticated-transaction response window, unchanged regular STUN requests, idempotence and expiry without network traffic.')
asyncio.run(main())
