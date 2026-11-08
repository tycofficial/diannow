"""Session-local latest-state gate for a lossy, unordered control stream.

This accepts only idempotent declarations, never keyboard, pointer, display
switch, disconnect, or other actions. Keep one instance per DesktopSession.
"""


class SessionStateGate:
    TYPES = frozenset(('settings', 'videoState', 'audioState', 'microphoneState'))

    def __init__(self):
        self.applied = {}

    def allows_legacy(self, event):
        # A delayed legacy message on the ordered stream must not undo a newer
        # declaration already accepted on the unordered stream.
        return event.get('type') not in self.applied

    @staticmethod
    def normalize(state):
        if not isinstance(state, dict) or state.get('type') not in SessionStateGate.TYPES:
            raise ValueError('Unsupported session state')
        kind = state['type']
        if kind == 'settings':
            quality, fps = state.get('quality'), state.get('fps')
            if quality not in ('480', '720', '1080', '2160'):
                raise ValueError('Invalid video quality')
            if type(fps) is not int or not 15 <= fps <= 120:
                raise ValueError('Invalid video frame rate')
            if type(state.get('fixedProfile')) is not bool:
                raise ValueError('Invalid fixed profile')
            return {'type': kind, 'quality': quality, 'fps': fps, 'fixedProfile': state['fixedProfile']}
        if type(state.get('enabled')) is not bool:
            raise ValueError('Invalid state flag')
        return {'type': kind, 'enabled': state['enabled']}

    def handle(self, event, apply_state):
        """Apply a newer declaration synchronously and return its ACK.

        The callback must return only after the in-memory state is accepted.
        Errors leave the revision uncommitted so a later retry can recover.
        """
        revision = event.get('revision')
        raw = event.get('state')
        kind = raw.get('type') if isinstance(raw, dict) else None
        if not isinstance(kind, str):
            kind = None
        reply = {'type': 'stateAck', 'stateType': kind, 'revision': revision,
                 'appliedRevision': self.applied.get(kind, (0, None))[0]}
        try:
            if event.get('type') != 'sessionState' or type(revision) is not int or not 1 <= revision <= 9007199254740991:
                raise ValueError('Invalid session state revision')
            state = self.normalize(raw)
            previous_revision, previous = self.applied.get(kind, (0, None))
            if revision < previous_revision:
                return reply
            if revision == previous_revision:
                if state != previous:
                    raise ValueError('Conflicting session state revision')
                return reply
            apply_state(state)
            self.applied[kind] = (revision, state)
            reply['appliedRevision'] = revision
        except Exception as error:
            reply['error'] = str(error)[:240]
        return reply
