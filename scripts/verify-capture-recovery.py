"""Exercise disposed DXGI resources, capture death and stop/read races."""
import threading
import time
from types import SimpleNamespace
from capture_recovery import RecoveringCamera


class Clock:
    now = 10.0
    def __call__(self):
        return self.now


class Camera:
    def __init__(self):
        self.is_capturing = False
        self.is_released = False
        self._duplicator = SimpleNamespace(duplicator=object())
        self._DXCamera__frame_available = threading.Event()
        self._DXCamera__frame_available.set()
        self.starts = 0
        self.stops = 0
        self.releases = 0
        self.reads = 0
        self.value = object()
    def start(self, **options):
        assert not self.is_released and not self.is_capturing
        self.starts += 1
        self.options = options
        self.is_capturing = True
    def stop(self):
        self.stops += 1
        self.is_capturing = False
        self._DXCamera__frame_available.set()
    def release(self):
        self.releases += 1
        self.stop()
        self.is_released = True
        self._duplicator.duplicator = None
    def get_latest_frame(self, **options):
        self.reads += 1
        return self.value


clock = Clock()
instances = []
def factory():
    camera = Camera()
    instances.append(camera)
    return camera

proxy = RecoveringCamera(factory, clock=clock)
assert not proxy.is_capturing
proxy.start(target_fps=60, video_mode=True)
first = instances[0]
assert proxy.get_latest_frame() is first.value
proxy.start(target_fps=60, video_mode=True)
assert len(instances) == first.starts == 1
proxy.stop()
assert not proxy.is_capturing and not first.is_released
proxy.start(target_fps=60, video_mode=True)
assert len(instances) == 1 and first.starts == 2

# Exact production failure: the DXGI pointer is disposed but DXcam itself is
# not released. Restarting the same inner camera must never be attempted.
first._duplicator.duplicator = None
first.is_capturing = False
assert proxy.is_capturing, 'Requested capture must remain pollable after producer death'
try:
    proxy.get_latest_frame()
except RuntimeError:
    pass
assert len(instances) == 1 and proxy.diagnostics['consecutiveFailures'] == 1
clock.now += 1.01
assert proxy.get_latest_frame() is instances[-1].value
assert len(instances) == 2 and first.releases == 1 and first.starts == 2
assert instances[-1].options == {'target_fps': 60, 'video_mode': True}
assert proxy.diagnostics['generation'] == 2 and proxy.diagnostics['recoveries'] == 1
second = instances[-1]
second.is_capturing = False
try:
    proxy.get_latest_frame()
except RuntimeError:
    pass
clock.now += 1.01
proxy.get_latest_frame()
assert len(instances) == 3 and second.is_released
proxy.release()
assert proxy.is_released and not proxy.is_capturing and proxy.get_latest_frame() is None
try:
    proxy.start(target_fps=60)
except RuntimeError:
    pass
else:
    raise AssertionError('A permanently released proxy must not restart')

# Failed creation is retried only after its bounded cooldown, not at the
# shared reader's 50ms retry frequency.
attempts = []
def flaky_factory():
    attempts.append(clock.now)
    if len(attempts) == 1:
        raise OSError('mock temporary display transition')
    return Camera()
flaky = RecoveringCamera(flaky_factory, clock=clock)
try:
    flaky.start(target_fps=60)
except OSError:
    pass
for _ in range(5):
    try:
        flaky.get_latest_frame()
    except RuntimeError:
        pass
assert len(attempts) == 1
clock.now += 1.01
assert flaky.get_latest_frame() is not None and len(attempts) == 2
flaky.release()

# Pinned DXcam's frame-ready event bounds waits even when the producer flag
# remains true but no frame arrives. Never enter its unbounded reader then.
stale_instances = []
def stale_factory():
    camera = Camera()
    stale_instances.append(camera)
    if len(stale_instances) == 1:
        camera._DXCamera__frame_available.clear()
    return camera
stale = RecoveringCamera(stale_factory, clock=clock)
stale.start(target_fps=60)
clock.now += 2.1
assert stale.get_latest_frame() is None and stale_instances[0].reads == 0
assert stale.diagnostics['failures'] == 1
clock.now += 1.1
assert stale.get_latest_frame() is stale_instances[-1].value
assert len(stale_instances) == 2 and stale_instances[0].is_released
stale.release()

# The state lock cannot cover a blocking frame read: stop() must be free to
# wake that read, and its old frame must not escape after stop.
entered = threading.Event()
wake = threading.Event()
class BlockingCamera(Camera):
    def get_latest_frame(self, **options):
        entered.set()
        assert wake.wait(.5), 'stop() could not wake the reader'
        return self.value
    def stop(self):
        super().stop()
        wake.set()
blocking = RecoveringCamera(BlockingCamera)
blocking.start(target_fps=60)
values = []
reader = threading.Thread(target=lambda: values.append(blocking.get_latest_frame()))
reader.start()
assert entered.wait(.5)
blocking.stop()
reader.join(.5)
assert not reader.is_alive() and values == [None]
blocking.release()

# Racing starts allocate one camera, and a failed old-camera release never
# starts an additional GPU owner beside it.
parallel_instances = []
def parallel_factory():
    camera = Camera()
    parallel_instances.append(camera)
    return camera
parallel = RecoveringCamera(parallel_factory)
workers = [threading.Thread(target=lambda: parallel.start(target_fps=60)) for _ in range(4)]
for worker in workers:worker.start()
for worker in workers:worker.join(.5)
assert len(parallel_instances) == 1 and parallel_instances[0].starts == 1
parallel.release()

failed_start_instances = []
class FailedStartCamera(Camera):
    def start(self, **options):
        raise OSError('mock start failure')
def failed_start_factory():
    camera = FailedStartCamera() if not failed_start_instances else Camera()
    failed_start_instances.append(camera)
    return camera
failed_start = RecoveringCamera(failed_start_factory, clock=clock)
try:
    failed_start.start(target_fps=60)
except OSError:
    pass
clock.now += 1.01
assert failed_start.get_latest_frame() is failed_start_instances[-1].value
assert len(failed_start_instances) == 2 and failed_start_instances[0].releases == 1
failed_start.release()

release_fail_instances = []
class FailedReleaseCamera(Camera):
    blocked = True
    def release(self):
        if self.blocked:
            raise RuntimeError('mock old producer cannot join')
        super().release()
def release_fail_factory():
    camera = FailedReleaseCamera() if not release_fail_instances else Camera()
    release_fail_instances.append(camera)
    return camera
release_fail = RecoveringCamera(release_fail_factory, clock=clock)
release_fail.start(target_fps=60)
release_fail_instances[0]._duplicator.duplicator = None
for advance in [0, 1.01]:
    clock.now += advance
    try:
        release_fail.get_latest_frame()
    except RuntimeError:
        pass
assert len(release_fail_instances) == 1, 'Never create a second GPU owner when the old one failed to release'
release_fail_instances[0].blocked = False
clock.now += 1.01
assert release_fail.get_latest_frame() is release_fail_instances[-1].value
assert len(release_fail_instances) == 2
release_fail.release()

# Exercise the actual shared reader guard, not just the proxy's direct API.
# A dead inner producer must not make SharedFrameSource skip it forever.
import numpy as np
from rtc_agent import SharedFrameSource
source_instances = []
class SourceCamera(Camera):
    def get_latest_frame(self, **options):
        time.sleep(.001)
        return super().get_latest_frame(**options)
def source_factory():
    camera = SourceCamera()
    camera.value = np.full((8, 8, 4), len(source_instances) + 1, dtype=np.uint8)
    source_instances.append(camera)
    return camera
source_proxy = RecoveringCamera(source_factory, retry_seconds=.01, stale_seconds=.1)
source = SharedFrameSource(source_proxy, hardware=True)
source_proxy.start(target_fps=60, video_mode=True)
try:
    pixels, captured, _, _ = source.get_yuv(8, 8)
    assert int(pixels[0, 0, 0]) == 1
    with source_proxy._lock:
        source_instances[0].is_capturing = False
        source_instances[0]._duplicator.duplicator = None
    assert source_proxy.is_capturing
    deadline = time.monotonic() + 1.5
    while source_proxy.diagnostics['generation'] < 2 and time.monotonic() < deadline:
        time.sleep(.01)
    assert source_proxy.diagnostics['generation'] == 2, 'SharedFrameSource skipped the dead producer instead of recreating it'
    pixels, _, _, _ = source.get_yuv(8, 8, after=time.perf_counter())
    assert int(pixels[0, 0, 0]) == 2 and source_instances[0].is_released
    assert source_proxy.diagnostics['recoveries'] == 1
finally:
    source.close()
    source_proxy.release()
    if source.reader is not None:
        source.reader.join(.5)
        assert not source.reader.is_alive()

print('Verified disposed DXGI/failed producer recreation, stable camera identity, normal stop/start reuse, bounded failed retries and stale polling, concurrent starts, stop/read wakeup, failed-start cleanup, failed-release ownership, real SharedFrameSource recovery and permanent cleanup. No desktop or network IO.')
