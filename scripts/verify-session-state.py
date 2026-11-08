"""Deterministic drop/reorder/duplicate coverage without desktop input."""
from session_state import SessionStateGate


def declaration(revision, kind='settings', **values):
    if kind == 'settings':
        values = {'quality': '1080', 'fps': 60, 'fixedProfile': True, **values}
    return {'type': 'sessionState', 'revision': revision, 'state': {'type': kind, **values}}


gate = SessionStateGate()
applied = []
first = declaration(1)
assert gate.allows_legacy(first['state'])
for _ in range(12):
    ack = gate.handle(first, applied.append)
    assert ack['appliedRevision'] == 1 and 'error' not in ack
assert len(applied) == 1, 'Lost ACK retries must not rebuild the video delivery policy'
assert not gate.allows_legacy({'type': 'settings'}), 'Delayed ordered fallback cannot undo new settings'
assert gate.allows_legacy({'type': 'button'}), 'Real input actions retain their normal path'
for revision, quality in [(4, '480'), (2, '1080'), (3, '720')]:
    gate.handle(declaration(revision, quality=quality), applied.append)
assert applied[-1]['quality'] == '480' and len(applied) == 2
assert 'error' in gate.handle(declaration(4, quality='1080'), applied.append)
for revision, enabled in [(10, True), (12, False), (11, True), (12, False)]:
    gate.handle(declaration(revision, 'microphoneState', enabled=enabled), applied.append)
assert [e['enabled'] for e in applied if e['type'] == 'microphoneState'] == [True, False]
assert not gate.allows_legacy({'type': 'microphoneState', 'enabled': True})
assert gate.allows_legacy({'type': 'audioState', 'enabled': True})
for event in [declaration(True), declaration(0), declaration(2**53), declaration(20, fps=True), declaration(21, quality='bad'), declaration(22, 'button', down=True), declaration(23, 'audioState', enabled='yes'), {'type': 'sessionState', 'revision': 24, 'state': {'type': []}}]:
    assert 'error' in gate.handle(event, applied.append)

def fail(_):
    raise RuntimeError('Temporary device failure')

assert 'error' in gate.handle(declaration(30, 'audioState', enabled=True), fail)
assert gate.allows_legacy({'type': 'audioState'})
assert gate.handle(declaration(30, 'audioState', enabled=True), applied.append)['appliedRevision'] == 30
assert len(gate.applied) <= 4
fresh = SessionStateGate()
assert fresh.handle(declaration(1, 'microphoneState', enabled=False), applied.append)['appliedRevision'] == 1
print({'latestStateGate': 'passed', 'maxStateEntries': 4, 'duplicateSettingsApplications': 1, 'desktopInputInjected': False})

# Exercise the actual DesktopSession integration with inert media/input owners.
from types import SimpleNamespace
from rtc_agent import DesktopSession, DeliveryPolicy

capture_requests, microphone_requests, streaming = [], [], []
desktop = DesktopSession.__new__(DesktopSession)
desktop.session_id = 'state-regression'
desktop.source_width, desktop.source_height, desktop.refresh = 1920, 1080, 120
desktop.video_enabled = True
desktop.pc = SimpleNamespace(connectionState='connected')
desktop.track = SimpleNamespace(width=852, height=480, fps=60, fixed_profile=True,
                                delivery=DeliveryPolicy(60, fixed=True), set_streaming=streaming.append)
desktop.owner = SimpleNamespace(ensure_capture_rate=capture_requests.append,
                                audio_router=SimpleNamespace(set_microphone=lambda *args: microphone_requests.append(args)))
desktop.audio_sender = SimpleNamespace(_enabled=False)
desktop.turn_flow = {'audioEnabled': False}
desktop.state_gate = SessionStateGate()
event = declaration(1)
for _ in range(12):
    assert desktop.state_gate.handle(event, desktop.apply_state)['appliedRevision'] == 1
    policy = desktop.track.delivery if _ == 0 else policy
    assert desktop.track.delivery is policy, 'Lost ACK retries cannot recreate the DeliveryPolicy'
assert capture_requests == [60]
assert (desktop.track.width, desktop.track.height, desktop.track.fps, desktop.track.fixed_profile) == (1920, 1080, 60, True)
late_legacy = {'type': 'settings', 'quality': '480', 'fps': 30, 'fixedProfile': False}
if desktop.state_gate.allows_legacy(late_legacy):
    desktop.apply_state(late_legacy)
assert desktop.track.height == 1080 and desktop.track.fps == 60 and desktop.track.fixed_profile
for index, kind in enumerate(('videoState', 'microphoneState', 'audioState'), 2):
    for enabled in (True, False):
        latest = declaration(index * 10 + (not enabled), kind, enabled=enabled)
        for _ in range(3):
            assert 'error' not in desktop.state_gate.handle(latest, desktop.apply_state)
assert microphone_requests == [('state-regression', True), ('state-regression', False)]
assert streaming == [True, False]
assert desktop.audio_sender._enabled is False and desktop.turn_flow['audioEnabled'] is False
assert desktop.track.delivery is policy and desktop.track.fps == 60 and desktop.track.fixed_profile
assert desktop.state_gate.handle(declaration(100, quality='480'), desktop.apply_state)['appliedRevision'] == 100
assert desktop.track.height == 480 and desktop.track.fps == 60 and desktop.track.fixed_profile
assert capture_requests == [60, 60]
print({'desktopSessionStateIntegration': 'passed', 'fixed60Preserved': True, 'actualDevicesOpened': False})
