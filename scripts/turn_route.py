"""Prefer the fastest proven UDP allocation among configured TURN endpoints.

Configuration and credentials stay in memory. A failed probe retains the exact
configured fallback entries; this module changes no shared aioice behaviour.
"""
import asyncio
import copy
import hashlib
import json
import time
from collections import OrderedDict

from aioice import Connection
from aioice.ice import StunProtocol, TransportPolicy, relayed_candidate
from aiortc.rtcicetransport import parse_stun_turn_uri

SUCCESS_TTL = 600
FAILURE_TTL = 60
PROBE_TIMEOUT = 3
MAX_CONCURRENT_PROBES = 3
_cache = OrderedDict()


class _RelayAllocationProbe(Connection):
    async def get_component_candidates(self, component, addresses, timeout=5):
        # aioice's general gatherer spawns an allocation task inside wait().
        # A cancelled outer gather would not own that task. This relay-only
        # probe awaits the allocation itself so cancellation closes its socket.
        candidate, protocol = await relayed_candidate(
            component=component,
            hide_related_address=True,
            protocol_factory=lambda: StunProtocol(self),
            turn_server=self.turn_server,
            turn_username=self.turn_username,
            turn_password=self.turn_password,
            turn_ssl=False,
            turn_transport='udp',
        )
        self._protocols.append(protocol)
        return [candidate]


async def _close_probe(connection):
    protocols = list(getattr(connection, '_protocols', ()))
    try:
        await asyncio.wait_for(connection.close(), timeout=1)
    except (Exception, asyncio.CancelledError):
        # TURN deletion can wait for a disconnected route. The installed
        # aioice delete() closes its underlying transport in finally, including
        # cancellation. Cancel only close tasks owned by this disposable probe.
        tasks = []
        for protocol in protocols:
            transport = getattr(protocol, 'transport', None)
            task = getattr(transport, '_TurnTransport__close_task', None)
            if task is not None and not task.done():
                task.cancel()
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def _probe_udp_allocation(host, port, username, credential):
    connection = _RelayAllocationProbe(
        ice_controlling=True,
        components=1,
        stun_server=None,
        turn_server=(host, port),
        turn_username=username,
        turn_password=credential,
        turn_transport='udp',
        turn_ssl=False,
        use_ipv6=False,
        transport_policy=TransportPolicy.RELAY,
    )
    try:
        started = time.perf_counter()
        await asyncio.wait_for(connection.gather_candidates(), PROBE_TIMEOUT)
        # Measure the allocation itself. TURN deletion / socket cleanup can
        # take a second and must not make a healthy endpoint appear slower.
        if any(candidate.type == 'relay' for candidate in connection.local_candidates):
            return max(.000001, time.perf_counter() - started)
        return None
    except Exception:
        return None
    finally:
        await _close_probe(connection)


def _configured_udp_endpoints(config):
    servers = config.get('iceServers', [])
    if not isinstance(servers, list):
        return []
    endpoints = []
    identities = set()
    for server in servers:
        if not isinstance(server, dict):
            continue
        urls = server.get('urls', [])
        urls = [urls] if isinstance(urls, str) else urls
        if not isinstance(urls, list):
            continue
        for url in urls:
            if not isinstance(url, str) or not url.startswith('turn:'):
                continue
            try:
                parsed = parse_stun_turn_uri(url)
                host, port = parsed['host'], parsed['port']
                if parsed['scheme'] != 'turn' or parsed['transport'] not in ('tcp', 'udp'):
                    continue
                if not 0 < port < 65536 or any(char.isspace() or char in '/@#[]\\' for char in host):
                    continue
            except (TypeError, ValueError, KeyError):
                continue
            # Change only the transport query. Preserve the configured host,
            # explicit/implicit port, credentials, and all original fallbacks.
            udp_url = url.split('?', 1)[0] + '?transport=udp'
            identity = hashlib.sha256(json.dumps((host, port, server.get('username'), server.get('credential')), sort_keys=True).encode()).digest()
            if identity not in identities:
                identities.add(identity)
                endpoints.append((server, udp_url, host, port))
    return endpoints


async def select_udp_turn_route(config, probe_fn=None, clock=time.monotonic):
    """Return a copied config; prepend the quickest proven configured UDP.

    ``probe_fn(host, port, username, credential)`` is an async bool or measured
    allocation-seconds probe for tests. Successful choices cache for ten
    minutes, failures for one minute;
    changes to any ICE endpoint or credential select a different cache entry.
    Added diagnostics contain only transport, cache, counts and allocation
    duration; no endpoint names or credentials are exposed.
    """
    result = copy.deepcopy(config)
    endpoints = _configured_udp_endpoints(config)
    if not endpoints:
        result['selectedTurnTransport'] = 'unchanged'
        result['turnRouteCached'] = False
        return result
    identity = hashlib.sha256(json.dumps(config.get('iceServers'), sort_keys=True, separators=(',', ':')).encode()).digest()
    now = clock()
    cached = _cache.get(identity)
    cache_hit = cached is not None and now < cached[1]
    if cache_hit:
        selected, _, allocation_ms, successes = cached
        _cache.move_to_end(identity)
    else:
        gate = asyncio.Semaphore(MAX_CONCURRENT_PROBES)

        async def probe(index, endpoint):
            server, _, host, port = endpoint
            async with gate:
                started = time.perf_counter()
                try:
                    outcome = await asyncio.wait_for(
                        (probe_fn or _probe_udp_allocation)(host, port, server.get('username'), server.get('credential')),
                        PROBE_TIMEOUT + (1.1 if probe_fn is None else 0))
                    if not outcome:
                        return None
                    # Test probes may return a bool. Production returns its
                    # measured allocation seconds, excluding its cleanup.
                    duration = outcome if isinstance(outcome, (int, float)) and not isinstance(outcome, bool) else time.perf_counter() - started
                    return index, max(0, duration) * 1000
                except Exception:
                    return None

        tasks = [asyncio.create_task(probe(index, endpoint)) for index, endpoint in enumerate(endpoints)]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        candidates = [candidate for candidate in results if candidate is not None]
        selected, allocation_ms = min(candidates, key=lambda candidate: candidate[1]) if candidates else (None, None)
        successes = len(candidates)
        _cache[identity] = (selected, clock() + (SUCCESS_TTL if selected is not None else FAILURE_TTL), allocation_ms, successes)
        _cache.move_to_end(identity)
        while len(_cache) > 32:
            _cache.popitem(last=False)
    success = selected is not None
    if success:
        server, udp_url, _, _ = endpoints[selected]
        result['iceServers'].insert(0, {**copy.deepcopy(server), 'urls': [udp_url]})
    result['selectedTurnTransport'] = 'udp' if success else 'tcp'
    result['turnRouteCached'] = cache_hit
    result['turnRouteCandidates'] = len(endpoints)
    result['turnRouteSucceeded'] = successes
    result['turnRouteAllocationMs'] = round(allocation_ms, 2) if allocation_ms is not None else None
    return result
