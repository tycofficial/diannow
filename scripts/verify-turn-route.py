"""Prove route selection and resource cleanup without sending network traffic."""
import asyncio
import copy
from types import SimpleNamespace
import turn_route as route


async def main():
    config = {'iceServers': [
        {'urls': 'stun:stun.example:3478'},
        {'urls': ['turn:relay.example:3478?transport=tcp', 'turns:relay.example:443'], 'username': 'test-user', 'credential': 'test-password'},
    ], 'other': {'preserved': True}}
    original = copy.deepcopy(config)
    calls = []
    now = [100.0]
    async def success(*args):
        calls.append(args)
        return True
    async def failure(*args):
        calls.append(args)
        return False
    route._cache.clear()
    chosen = await route.select_udp_turn_route(config, success, lambda: now[0])
    assert chosen['selectedTurnTransport'] == 'udp' and not chosen['turnRouteCached']
    assert chosen['iceServers'][0] == {'urls': ['turn:relay.example:3478?transport=udp'], 'username': 'test-user', 'credential': 'test-password'}
    assert chosen['iceServers'][1:] == original['iceServers'] and config == original
    assert calls == [('relay.example', 3478, 'test-user', 'test-password')]
    cached = await route.select_udp_turn_route(config, failure, lambda: now[0] + 599)
    assert cached['selectedTurnTransport'] == 'udp' and cached['turnRouteCached'] and len(calls) == 1
    now[0] += 601
    failed = await route.select_udp_turn_route(config, failure, lambda: now[0])
    assert failed['iceServers'] == original['iceServers'] and failed['selectedTurnTransport'] == 'tcp' and len(calls) == 2
    await route.select_udp_turn_route(config, success, lambda: now[0] + 59)
    assert len(calls) == 2
    now[0] += 61
    assert (await route.select_udp_turn_route(config, success, lambda: now[0]))['selectedTurnTransport'] == 'udp'
    assert len(calls) == 3
    changed = copy.deepcopy(config)
    changed['iceServers'][1]['credential'] = 'rotated-test-password'
    await route.select_udp_turn_route(changed, success, lambda: now[0])
    assert len(calls) == 4 and calls[-1][-1] == 'rotated-test-password'
    changed['iceServers'][1]['urls'][0] = 'turn:relay.example:5349?transport=tcp'
    assert (await route.select_udp_turn_route(changed, success, lambda: now[0]))['iceServers'][0]['urls'] == ['turn:relay.example:5349?transport=udp']
    assert calls[-1][1] == 5349

    for invalid in ['turns:relay.example:443', 'turn:relay.example:99999?transport=tcp', 'turn:relay.example:0?transport=tcp', 'turn:user@relay.example:3478?transport=tcp', 'turn:relay.example:3478?transport=tcp&extra=1', 'turn:relay.example/path?transport=tcp']:
        before = len(calls)
        original_bad = {'iceServers': [{'urls': invalid, 'username': 'test', 'credential': 'test'}]}
        unmodified = await route.select_udp_turn_route(original_bad, success, lambda: now[0])
        assert unmodified['iceServers'] == original_bad['iceServers'] and unmodified['selectedTurnTransport'] == 'unchanged'
        assert len(calls) == before, 'Malformed or TLS entries must never synthesize UDP'
    route._cache.clear()
    async def raises(*args):
        raise OSError('mock route failure')
    assert (await route.select_udp_turn_route(config, raises))['iceServers'] == config['iceServers']
    route._cache.clear()
    async def cancelled(*args):
        raise asyncio.CancelledError
    try:
        await route.select_udp_turn_route(config, cancelled)
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError('Cancelling selection must propagate')
    assert not route._cache

    real_connection = route._RelayAllocationProbe
    instances = []
    class FakeConnection:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.local_candidates = []
            self.closed = False
            instances.append(self)
        async def gather_candidates(self):
            if len(instances) == 1:
                self.local_candidates = [SimpleNamespace(type='relay')]
            else:
                raise OSError('mock allocation failure')
        async def close(self):
            self.closed = True
    route._RelayAllocationProbe = FakeConnection
    try:
        assert await route._probe_udp_allocation('relay.example', 3478, 'test', 'test')
        assert not await route._probe_udp_allocation('relay.example', 3478, 'test', 'test')
        assert all(instance.closed for instance in instances)
        assert all(instance.kwargs['transport_policy'] == route.TransportPolicy.RELAY and instance.kwargs['stun_server'] is None and instance.kwargs['turn_transport'] == 'udp' for instance in instances)
    finally:
        route._RelayAllocationProbe = real_connection
    print('Verified same-endpoint proven UDP priority, exact fallback preservation, credential/endpoint cache identity, TTLs, malformed/TLS rejection, cancellation and owned probe cleanup. No network traffic.')


asyncio.run(main())
