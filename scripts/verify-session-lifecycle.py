"""Verify session cleanup and failed-publication retries without desktop IO."""
import asyncio
import json
import time
from types import SimpleNamespace

import rtc_agent


class SessionDouble(rtc_agent.DesktopSession):
    answer_calls=0

    async def answer(self,offer):
        type(self).answer_calls+=1
        self.pc=SimpleNamespace(connectionState='connected')
        return {'type':'answer','sdp':'answer-for-'+self.session_id}

    async def close(self):
        self.closed=True
        if self in self.owner.sessions:self.owner.sessions.remove(self)


async def main():
    rtc_agent.DesktopSession=SessionDouble
    agent=rtc_agent.DesktopAgent.__new__(rtc_agent.DesktopAgent)
    agent.camera=None;agent.input=None;agent.source_width=1920;agent.source_height=1080;agent.refresh=60;agent.sessions=[]
    offer={'type':'offer','sdp':'synthetic-offer'}
    answer=await agent.answer(offer,session_id='retry-session')
    # Model an answer POST failure: the pending request is returned again.
    again=await agent.answer(offer,session_id='retry-session')
    assert again is answer and SessionDouble.answer_calls==1
    assert len(agent.sessions)==1,'Failed publication created a second session.'
    now=time.monotonic();session=agent.sessions[0]
    session.created_at=now-600;session.last_activity=now
    assert not session.expired(now),'An active viewer must survive regardless of age.'
    session.last_activity=now-31
    assert session.expired(now),'A viewer without heartbeat must release its slot.'
    session.last_activity=now
    session.pc.connectionState='disconnected'
    assert not session.expired(now),'Short disconnections must have time to recover.'
    assert session.expired(now+31)
    session.pc.connectionState='connecting';session.created_at=now-36
    assert session.expired(now),'An abandoned connection attempt must release its slot.'
    session.pc.connectionState='connected';session.last_activity=now-31
    await agent.answer(offer,session_id='new-0')
    assert session.closed and len(agent.sessions)==1
    for index in range(1,4):await agent.answer(offer,session_id='new-'+str(index))
    active=list(agent.sessions)
    cached=await agent.answer(offer,session_id='new-0')
    assert cached['type']=='answer' and len(agent.sessions)==4
    try:await agent.answer(offer,session_id='fifth-viewer')
    except RuntimeError as error:assert 'Too many active' in str(error)
    else:raise AssertionError('A fifth active viewer must still respect the limit.')
    assert agent.sessions==active and all(not session.closed for session in active)
    print(json.dumps({'answerPublicationRetryReusedSession':True,'staleSessionsReclaimed':True,'fourActiveViewersPreserved':True,'desktopIO':False,'networkTraffic':False}))


asyncio.run(main())
