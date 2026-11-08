"""Serialize explicit display selection for a shared desktop capture process.

No Windows or filesystem operations live here. ``refresh`` asynchronously
enumerates displays; ``apply(mode, resolution, changed)`` asynchronously
persists the preference and, for a changed virtual target, activates its mode.
The caller sends the ACK before claiming and scheduling a process restart.
"""
import asyncio
from collections import OrderedDict


class DisplaySwitchCoordinator:
    def __init__(self, *, refresh, apply, limit=32):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError('Display request cache must hold between 1 and 32 entries')
        self.refresh = refresh
        self.apply = apply
        self.limit = limit
        self.cache = OrderedDict()
        self.latest = {}
        self.pending = None
        self.restarting = None
        self.forgotten = set()

    @staticmethod
    def _signature(event):
        mode = event.get('mode')
        resolution = event.get('resolution', '1080')
        return (mode if isinstance(mode, str) and len(mode) <= 32 else None,
                resolution if isinstance(resolution, str) and len(resolution) <= 32 else None)

    def _remember(self, key, signature, reply):
        self.cache[key] = {'signature': signature, 'reply': dict(reply), 'claimed': False}
        self.cache.move_to_end(key)
        while len(self.cache) > self.limit:
            # Busy replies must not evict the ACK needed to claim the one
            # outstanding restart, even if several viewers retry at once.
            victim = next(item for item in self.cache if item != self.restarting)
            del self.cache[victim]
        return dict(reply)

    @staticmethod
    def _target(items, mode):
        if mode == 'virtual':
            target = next((item for item in items if item.get('virtual') is True), None)
            if target is None:
                raise RuntimeError('Virtual display is unavailable; no display was substituted')
            return target
        physical = [item for item in items if item.get('virtual') is False
                    and item.get('attached') and len(item.get('bounds', ())) >= 4
                    and item['bounds'][2] > 0 and item['bounds'][3] > 0]
        target = next((item for item in physical if item.get('primary')), None)
        if target is None and physical:
            target = physical[0]
        if target is None:
            raise RuntimeError('Physical display is unavailable; no virtual display was substituted')
        return target

    @staticmethod
    def _same(current, target, mode, resolution):
        if not isinstance(current, dict) or not current.get('attached', True):
            return False
        if bool(current.get('virtual')) != (mode == 'virtual'):
            return False
        current_size = tuple(current.get('bounds', ()))[2:4]
        target_size = (3840, 2160) if resolution == '2160' else (1920, 1080)
        if mode == 'original':
            target_size = tuple(target.get('bounds', ()))[2:4]
        elif tuple(target.get('bounds', ()))[2:4] != target_size:
            # Capture metadata can predate an external display-mode change.
            # The freshly enumerated Windows mode must match as well.
            return False
        return (current.get('name') == target.get('name') and len(current_size) == 2
                and current_size == target_size and bool(target.get('attached')))

    async def request(self, owner, event, current_display):
        """Return a copied ACK; reject conflicting / old / concurrent requests.

        Each live session owns monotonically increasing positive safe integer
        request IDs. Failed requests consume their ID too. The caller must use
        a new ID after a busy/error ACK; an identical retry is idempotent.
        Call ``forget_owner`` only when that session can no longer send input.
        """
        event = event if isinstance(event, dict) else {}
        request_id = event.get('requestId')
        signature = self._signature(event)
        mode, resolution = signature
        reply = {'type': 'displayAck', 'requestId': request_id,
                 'mode': mode, 'resolution': resolution, 'stage': 'applied'}
        if (not isinstance(owner, str) or not owner or len(owner) > 128
                or type(request_id) is not int or not 1 <= request_id <= 9007199254740991):
            return {**reply, 'error': 'Invalid display request identity'}
        key = (owner, request_id)
        cached = self.cache.get(key)
        if cached is not None:
            if signature != cached['signature']:
                return {**reply, 'error': 'Conflicting display request ID'}
            return dict(cached['reply'])
        if self.pending is not None and self.pending['key'] == key:
            if signature != self.pending['signature']:
                return {**reply, 'error': 'Conflicting display request ID'}
            # Do not let a disconnected caller cancel shared device work.
            return dict(await asyncio.shield(self.pending['task']))
        previous = self.latest.get(owner, 0)
        if request_id <= previous:
            return {**reply, 'error': 'Stale display request ID'}
        if owner not in self.latest and len(self.latest) >= self.limit:
            return {**reply, 'error': 'Too many display request owners'}
        self.latest[owner] = request_id
        if mode not in ('original', 'virtual') or resolution not in ('1080', '2160'):
            return self._remember(key, signature, {**reply, 'error': 'Invalid display target'})
        if self.pending is not None or self.restarting is not None:
            return self._remember(key, signature, {**reply, 'error': 'Another display switch is pending; retry with a new request ID'})

        async def work():
            result = dict(reply)
            try:
                target = self._target(await self.refresh(), mode)
                if self.latest.get(owner) != request_id:
                    raise RuntimeError('Display request was superseded before applying')
                changed = not self._same(current_display, target, mode, resolution)
                await self.apply(mode, resolution, changed)
                result['stage'] = 'restarting' if changed else 'applied'
                if changed:
                    self.restarting = key
            except Exception as error:
                result['error'] = str(error)[:240]
            finally:
                self.pending = None
                if owner in self.forgotten:
                    self.latest.pop(owner, None)
                    self.forgotten.discard(owner)
            return self._remember(key, signature, result)

        task = asyncio.create_task(work())
        self.pending = {'key': key, 'signature': signature, 'task': task}
        return dict(await asyncio.shield(task))

    def claim_restart(self, owner, request_id):
        """After sending the ACK, claim one delayed restart-marker write."""
        key = (owner, request_id)
        item = self.cache.get(key)
        if (self.restarting != key or item is None or item['claimed']
                or item['reply'].get('error') or item['reply']['stage'] != 'restarting'):
            return False
        item['claimed'] = True
        return True

    def restart_failed(self, owner, request_id, error):
        key = (owner, request_id)
        if self.restarting == key:
            self.restarting = None
            item = self.cache.get(key)
            if item is not None:
                item['reply']['error'] = str(error)[:240]

    def forget_owner(self, owner):
        if self.pending is not None and self.pending['key'][0] == owner:
            self.forgotten.add(owner)
        else:
            self.latest.pop(owner, None)
