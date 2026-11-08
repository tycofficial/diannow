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

    # Evaluate every existing endpoint, including native UDP. Deduplicate TCP
    # and UDP variants with the same credentials, keep every original entry,
    # and use allocation time rather than slow cleanup time for the ranking.
    multi = {'iceServers': [
        {'urls': ['turn:slow.example:3478?transport=tcp', 'turn:slow.example:3478?transport=udp'], 'username': 'a', 'credential': 'a'},
        {'urls': 'turn:failed.example:3478?transport=udp', 'username': 'b', 'credential': 'b'},
        {'urls': 'turn:quick.example:3478?transport=udp', 'username': 'c', 'credential': 'c'},
    ]}
    untouched = copy.deepcopy(multi)
    measured_calls = []
    async def measured(host, *args):
        measured_calls.append(host)
        if host == 'quick.example':
            await asyncio.sleep(.02) # represents teardown after a 5ms allocation
            return .005
        if host == 'slow.example':return .2
        return False
    route._cache.clear()
    fastest = await route.select_udp_turn_route(multi, measured)
    assert fastest['iceServers'][0]['urls'] == ['turn:quick.example:3478?transport=udp']
    assert fastest['iceServers'][1:] == untouched['iceServers'] and multi == untouched
    assert fastest['turnRouteCandidates'] == 3 and fastest['turnRouteSucceeded'] == 2
    assert fastest['turnRouteAllocationMs'] == 5
    assert len(measured_calls) == 3, 'TCP and UDP variants must not create duplicate allocations'
    fastest_cached = await route.select_udp_turn_route(multi, failure)
    assert fastest_cached['turnRouteCached'] and fastest_cached['turnRouteAllocationMs'] == 5
    assert fastest_cached['iceServers'][0] == fastest['iceServers'][0]

    # Configured lists can grow without spawning unlimited socket probes.
    many = {'iceServers': [{'urls': f'turn:relay-{index}.example:3478?transport=udp'} for index in range(8)]}
    active = 0
    maximum_active = 0
    total = 0
    async def bounded(*args):
        nonlocal active, maximum_active, total
        active += 1; total += 1
        maximum_active = max(maximum_active, active)
        try:
            await asyncio.sleep(.005)
            return True
        finally:active -= 1
    route._cache.clear()
    await route.select_udp_turn_route(many, bounded)
    assert total == 8 and maximum_active == route.MAX_CONCURRENT_PROBES and active == 0

    started = asyncio.Event()
    cleaned = []
    async def waiting(host, *args):
        started.set()
        try:await asyncio.Event().wait()
        finally:cleaned.append(host)
    route._cache.clear()
    selection = asyncio.create_task(route.select_udp_turn_route(many, waiting))
    await started.wait();await asyncio.sleep(0)
    selection.cancel()
    try:await selection
    except asyncio.CancelledError:pass
    else:raise AssertionError('Cancelling concurrent selection must propagate')
    assert len(cleaned) == route.MAX_CONCURRENT_PROBES and not route._cache

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
    print('Verified fastest configured UDP allocation, timing excludes cleanup, all endpoints/fallbacks preserved, duplicate variants coalesced, bounded concurrency, credential/endpoint cache identity, TTLs, malformed/TLS rejection, cancellation and owned probe cleanup. No network traffic.')


asyncio.run(main())
