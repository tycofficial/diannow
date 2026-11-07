"""Prefer a proven UDP allocation on the already configured TURN endpoint.

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
        await asyncio.wait_for(connection.gather_candidates(), PROBE_TIMEOUT)
        return any(candidate.type == 'relay' for candidate in connection.local_candidates)
    except Exception:
        return False
    finally:
        await _close_probe(connection)


def _configured_tcp_endpoint(config):
    servers = config.get('iceServers', [])
    if not isinstance(servers, list):
        return None
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
                if parsed['scheme'] != 'turn' or parsed['transport'] != 'tcp':
                    continue
                if not 0 < port < 65536 or any(char.isspace() or char in '/@#[]\\' for char in host):
                    continue
            except (TypeError, ValueError, KeyError):
                continue
            # Change only the transport query. Preserve the configured host,
            # explicit/implicit port, credentials, and all original fallbacks.
            udp_url = url.rsplit('?transport=', 1)[0] + '?transport=udp'
            return server, udp_url, host, port
    return None


async def select_udp_turn_route(config, probe_fn=None, clock=time.monotonic):
    """Return a copied config; prepend same-endpoint UDP only after proof.

    ``probe_fn(host, port, username, credential)`` is an async bool probe for
    tests. Successful results cache for ten minutes, failures for one minute;
    changes to any ICE endpoint or credential select a different cache entry.
    The only added diagnostic fields contain a transport name and cache flag.
    """
    result = copy.deepcopy(config)
    endpoint = _configured_tcp_endpoint(config)
    if endpoint is None:
        result['selectedTurnTransport'] = 'unchanged'
        result['turnRouteCached'] = False
        return result
    server, udp_url, host, port = endpoint
    identity = hashlib.sha256(json.dumps(config.get('iceServers'), sort_keys=True, separators=(',', ':')).encode()).digest()
    now = clock()
    cached = _cache.get(identity)
    cache_hit = cached is not None and now < cached[1]
    if cache_hit:
        success = cached[0]
        _cache.move_to_end(identity)
    else:
        try:
            success = bool(await asyncio.wait_for(
                (probe_fn or _probe_udp_allocation)(host, port, server.get('username'), server.get('credential')),
                PROBE_TIMEOUT + (1.1 if probe_fn is None else 0),
            ))
        except Exception:
            success = False
        _cache[identity] = (success, clock() + (SUCCESS_TTL if success else FAILURE_TTL))
        _cache.move_to_end(identity)
        while len(_cache) > 32:
            _cache.popitem(last=False)
    if success:
        result['iceServers'].insert(0, {**copy.deepcopy(server), 'urls': [udp_url]})
    result['selectedTurnTransport'] = 'udp' if success else 'tcp'
    result['turnRouteCached'] = cache_hit
    return result
