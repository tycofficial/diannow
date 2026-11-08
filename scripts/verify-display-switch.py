"""Display selection orchestration with fake enumeration, devices and storage."""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from display_switch import DisplaySwitchCoordinator


PHYSICAL = {'name': 'physical', 'virtual': False, 'attached': True,
            'primary': True, 'bounds': [0, 0, 1366, 768]}
VIRTUAL = {'name': 'virtual', 'virtual': True, 'attached': True,
           'primary': False, 'bounds': [1366, 0, 1920, 1080]}


def event(request_id, mode='original', resolution='1080'):
    return {'type': 'display', 'requestId': request_id, 'mode': mode, 'resolution': resolution}


def fixture(*, items=None, limit=32, gate=None, failure=None):
    calls = []
    async def refresh():
        if gate is not None:await gate.wait()
        return deepcopy(items if items is not None else [PHYSICAL, VIRTUAL])
    async def apply(mode, resolution, changed):
        calls.append((mode, resolution, changed))
        if failure is not None:raise OSError(failure)
    return DisplaySwitchCoordinator(refresh=refresh, apply=apply, limit=limit), calls


async def verify_session_integration():
    # Import the real method without constructing a DesktopAgent, camera,
    # input driver, sound endpoint or actual restart path.
    from aiortc.exceptions import InvalidStateError
    from rtc_agent import DesktopSession

    class Marker:
        def __init__(self, log, fail=False):self.log=log;self.fail=fail
        def __truediv__(self, name):
            assert name == 'restart.request'
            return self
        def touch(self):
            self.log.append(('marker', None))
            if self.fail:raise OSError('simulated restart marker failure')

    class Channel:
        def __init__(self, log, state='open', fail=False):self.log=log;self.readyState=state;self.fail=fail
        def send(self, data):
            if self.fail:raise InvalidStateError('simulated close during ACK')
            self.log.append(('ack', json.loads(data)))

    def session(manager, current):
        value=DesktopSession.__new__(DesktopSession)
        value.session_id='integration-owner'
        value.owner=SimpleNamespace(display_switch=manager,display=deepcopy(current))
        return value

    manager, calls=fixture();log=[];desktop=session(manager,PHYSICAL)
    with patch('rtc_agent.DISPLAY_STATE',Marker(log)):
        await asyncio.gather(desktop.change_display(event(1,'virtual'),Channel(log)),
                             desktop.change_display(event(1,'virtual'),Channel(log)))
        assert [kind for kind,_ in log].count('marker')==1
        assert log[0][0]=='ack' and log[-1][0]=='marker'
        assert all(value['stage']=='restarting' for kind,value in log if kind=='ack')
        await desktop.change_display(event(1,'virtual'),Channel(log))
        assert [kind for kind,_ in log].count('marker')==1
        assert calls==[('virtual','1080',True)]

    for failure in (None,'simulated preference rejection'):
        manager,calls=fixture(failure=failure);log=[];desktop=session(manager,PHYSICAL)
        with patch('rtc_agent.DISPLAY_STATE',Marker(log)):
            await desktop.change_display(event(1),Channel(log))
        assert len(log)==1 and log[0][0]=='ack'
        assert bool(log[0][1].get('error'))==bool(failure)
        assert calls==[('original','1080',False)]

    # The accepted selection must still restart capture if the viewer closes
    # before its ACK, including aiortc's specific send-time exception.
    for state,fail in (('closed',False),('open',True)):
        manager,calls=fixture();log=[];desktop=session(manager,PHYSICAL)
        with patch('rtc_agent.DISPLAY_STATE',Marker(log)):
            await desktop.change_display(event(1,'virtual'),Channel(log,state,fail))
        assert log==[('marker',None)] and calls==[('virtual','1080',True)]

    manager,calls=fixture();log=[];desktop=session(manager,PHYSICAL)
    with patch('rtc_agent.DISPLAY_STATE',Marker(log,fail=True)):
        await desktop.change_display(event(1,'virtual'),Channel(log))
    assert [kind for kind,_ in log]==['ack','marker','ack']
    assert log[-1][1].get('error') and manager.restarting is None
    assert not manager.claim_restart('integration-owner',1)


async def main():
    coordinator, calls = fixture()
    same = await coordinator.request('one', event(1), deepcopy(PHYSICAL))
    assert same['stage'] == 'applied' and not same.get('error')
    assert calls == [('original', '1080', False)], 'Persist even unchanged physical capture'
    assert not coordinator.claim_restart('one', 1)
    same['stage'] = 'corrupted-by-caller'
    assert (await coordinator.request('one', event(1), PHYSICAL))['stage'] == 'applied'
    assert len(calls) == 1, 'Duplicate IDs must not persist or activate twice'
    conflict = await coordinator.request('one', event(1, 'virtual'), PHYSICAL)
    assert conflict.get('error') and len(calls) == 1
    wrong = await coordinator.request('one', event(3, 'unsupported'), PHYSICAL)
    assert wrong.get('error')
    assert (await coordinator.request('one', event(2, 'virtual'), PHYSICAL)).get('error')
    assert len(calls) == 1, 'A newer failed request fences older commands'

    switched = await coordinator.request('one', event(4, 'virtual', '2160'), PHYSICAL)
    assert switched['stage'] == 'restarting' and not switched.get('error')
    assert calls[-1] == ('virtual', '2160', True)
    assert (await coordinator.request('two', event(1), VIRTUAL)).get('error')
    assert len(calls) == 2, 'Do not switch again during ACK-to-restart grace period'
    assert coordinator.claim_restart('one', 4)
    assert not coordinator.claim_restart('one', 4)
    assert (await coordinator.request('one', event(4, 'virtual', '2160'), PHYSICAL)) == switched
    assert not coordinator.claim_restart('one', 4)
    coordinator.restart_failed('one', 4, 'simulated marker write failure')
    assert (await coordinator.request('one', event(4, 'virtual', '2160'), PHYSICAL)).get('error')
    assert (await coordinator.request('one', event(5), PHYSICAL))['stage'] == 'applied'

    for current, resolution, expected in [(VIRTUAL, '1080', False),
                                          (VIRTUAL, '2160', True),
                                          ({**VIRTUAL, 'attached': False}, '1080', True)]:
        item, recorded = fixture()
        result = await item.request('one', event(1, 'virtual', resolution), current)
        assert recorded == [('virtual', resolution, expected)]
        assert result['stage'] == ('restarting' if expected else 'applied')
    external, recorded = fixture(items=[PHYSICAL, {**VIRTUAL, 'bounds': [1366, 0, 3840, 2160]}])
    assert (await external.request('one', event(1, 'virtual'), VIRTUAL))['stage'] == 'restarting'
    assert recorded == [('virtual', '1080', True)], 'Stale capture metadata cannot hide a changed OS mode'
    missing, recorded = fixture(items=[VIRTUAL])
    assert (await missing.request('one', event(1), VIRTUAL)).get('error')
    assert not recorded, 'No physical target must never silently select virtual'
    missing, recorded = fixture(items=[PHYSICAL])
    assert (await missing.request('one', event(1, 'virtual'), PHYSICAL)).get('error')
    assert not recorded, 'No virtual target must never silently select physical'
    failed, recorded = fixture(failure='simulated driver rejection')
    first = await failed.request('one', event(1, 'virtual'), PHYSICAL)
    assert first.get('error') and not failed.claim_restart('one', 1)
    assert await failed.request('one', event(1, 'virtual'), PHYSICAL) == first
    assert len(recorded) == 1

    # Identical concurrent retries share one work item. Independent requests
    # get an immediate error and do not queue behind driver work.
    gate = asyncio.Event()
    parallel, recorded = fixture(gate=gate)
    first = asyncio.create_task(parallel.request('one', event(1, 'virtual'), PHYSICAL))
    await asyncio.sleep(0)
    duplicate = asyncio.create_task(parallel.request('one', event(1, 'virtual'), PHYSICAL))
    await asyncio.sleep(0)
    assert (await parallel.request('two', event(1), PHYSICAL)).get('error')
    gate.set()
    a, b = await asyncio.gather(first, duplicate)
    assert a == b and a['stage'] == 'restarting'
    assert recorded == [('virtual', '1080', True)]
    # Rejected higher revisions also fence a pending request that has not
    # started its device operations yet.
    gate = asyncio.Event()
    fenced, recorded = fixture(gate=gate)
    task = asyncio.create_task(fenced.request('one', event(1, 'virtual'), PHYSICAL))
    await asyncio.sleep(0)
    assert (await fenced.request('one', event(2, 'invalid'), PHYSICAL)).get('error')
    gate.set()
    assert (await task).get('error') and not recorded

    bounded, recorded = fixture(limit=4)
    assert (await bounded.request('one', event(1, 'virtual'), PHYSICAL))['stage'] == 'restarting'
    for index in range(2, 100):
        assert (await bounded.request('one', event(index), PHYSICAL)).get('error')
        assert len(bounded.cache) <= 4
    assert bounded.claim_restart('one', 1), 'Busy retries cannot evict outstanding restart'
    assert (await bounded.request('one', event(2), PHYSICAL)).get('error')
    assert len(recorded) == 1
    owners, _ = fixture(limit=2)
    await owners.request('one', event(1), PHYSICAL)
    await owners.request('two', event(1), PHYSICAL)
    assert (await owners.request('three', event(1), PHYSICAL)).get('error')
    owners.forget_owner('one')
    assert not (await owners.request('three', event(2), PHYSICAL)).get('error')
    for invalid in (None, 0, -1, True, 1.5, 9007199254740992):
        assert (await owners.request('two', event(invalid), PHYSICAL)).get('error')
    await verify_session_integration()
    print('Verified unchanged-original persistence, physical/virtual target validation, mode/size changes, ACK-before-restart claim, no silent fallback, idempotency, revision fences, busy rejection, bounded caches, owner cleanup, driver failure and real DesktopSession integration including closed channels / failed marker writes. No Windows displays, files or input were changed.')


asyncio.run(main())
