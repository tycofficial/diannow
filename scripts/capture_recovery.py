"""Own and rebuild one DXcam camera after its capture resources are lost.

DXcam can stop its producer after output recovery released the duplicator.
Calling start() on that same camera does not rebuild the disposed resources.
Keep one stable public camera identity while releasing/recreating its inner
camera. No factory singleton or library class is modified.
"""
import threading
import time


class RecoveringCamera:
    def __init__(self, factory, retry_seconds=1.0, stale_seconds=2.0,
                 clock=time.monotonic, log=None):
        self._factory = factory
        self._retry_seconds = retry_seconds
        self._stale_seconds = stale_seconds
        self._clock = clock
        self._log = log
        self._lock = threading.RLock()
        self._camera = None
        self._requested = False
        self._released = False
        self._running_expected = False
        self._invalid = False
        self._retry_at = 0.0
        self._last_frame = None
        self._unconfirmed_recovery = False
        self._options = {}
        self._stats = {'generation': 0, 'recoveries': 0, 'failures': 0,
                       'consecutiveFailures': 0, 'lastError': None}

    @property
    def is_capturing(self):
        # SharedFrameSource must keep calling get_latest_frame() when capture
        # was requested, even if DXcam's failed producer set its own flag false.
        with self._lock:
            return self._requested and not self._released

    @property
    def is_released(self):
        with self._lock:
            return self._released

    @property
    def diagnostics(self):
        with self._lock:
            return dict(self._stats, requested=self._requested)

    def _disposed(self, camera):
        if getattr(camera, 'is_released', False):
            return True
        duplicator = getattr(camera, '_duplicator', None)
        # WGC implementations need not have a Desktop Duplication pointer.
        if duplicator is not None and hasattr(duplicator, 'duplicator'):
            pointer = duplicator.duplicator
            if pointer is None or not pointer:
                return True
        return False

    def _failed(self, error):
        self._invalid = True
        self._retry_at = self._clock() + self._retry_seconds
        self._stats['failures'] += 1
        self._stats['consecutiveFailures'] += 1
        self._stats['lastError'] = type(error).__name__
        if self._log:
            self._log({'captureRecoveryError': type(error).__name__,
                       'consecutiveFailures': self._stats['consecutiveFailures']})

    def _release_inner(self):
        camera = self._camera
        if camera is None:
            return
        # DXcam.release() stops/joins its own producer before freeing GPU
        # resources and marks the cached instance released for factory reuse.
        camera.release()
        thread = getattr(camera, '_DXCamera__thread', None)
        if thread is not None and thread.is_alive():
            raise RuntimeError('Old desktop capture producer has not stopped')
        self._camera = None
        self._running_expected = False

    def _ensure_started(self):
        if not self._requested or self._released:
            return None
        camera = self._camera
        broken = camera is not None and (
            self._invalid or self._disposed(camera) or
            self._running_expected and not camera.is_capturing)
        if broken and not self._invalid:
            self._failed(RuntimeError('Desktop capture producer or resources were lost'))
        if camera is not None and not broken and camera.is_capturing:
            return camera
        if self._clock() < self._retry_at:
            raise RuntimeError('Desktop capture recovery is waiting to retry')
        try:
            if broken:
                self._release_inner()
                camera = None
            created = camera is None
            if created:
                camera = self._factory()
                self._camera = camera
            camera.start(**self._options)
            self._running_expected = True
            self._invalid = False
            self._last_frame = self._clock()
            if created:
                self._stats['generation'] += 1
                self._unconfirmed_recovery = self._stats['generation'] > 1
            if self._log and created:
                self._log({'captureGeneration': self._stats['generation'],
                           'captureRecoveries': self._stats['recoveries']})
            return camera
        except Exception as error:
            self._failed(error)
            raise

    def start(self, **options):
        with self._lock:
            if self._released:
                raise RuntimeError('Desktop capture has been released')
            self._options = dict(options)
            self._requested = True
            self._ensure_started()

    def get_latest_frame(self, **options):
        with self._lock:
            camera = self._ensure_started()
        if camera is None:
            return None
        # Pinned DXcam 0.3 exposes this Event. Wait in bounded steps before
        # entering its otherwise unbounded get_latest_frame() loop. Only this
        # shared reader consumes frames, so an available event cannot be stolen
        # by another consumer. stop() remains free to wake the reader.
        available = getattr(camera, '_DXCamera__frame_available', None)
        if available is not None and not available.wait(.1):
            frame = None
        else:
            with self._lock:
                if camera is not self._camera or not self._requested:
                    return None
            try:
                frame = camera.get_latest_frame(**options)
            except Exception as error:
                with self._lock:
                    if camera is self._camera and self._requested:
                        self._failed(error)
                    else:
                        return None
                raise
        with self._lock:
            if camera is not self._camera or not self._requested:
                return None
            if frame is not None:
                self._last_frame = self._clock()
                self._stats['consecutiveFailures'] = 0
                self._stats['lastError'] = None
                if self._unconfirmed_recovery:
                    self._unconfirmed_recovery = False
                    self._stats['recoveries'] += 1
                    if self._log:
                        self._log({'captureRecovered': True,
                                   'captureGeneration': self._stats['generation'],
                                   'captureRecoveries': self._stats['recoveries']})
            elif self._last_frame is not None and self._clock() - self._last_frame >= self._stale_seconds:
                self._failed(RuntimeError('Desktop capture producer stopped delivering frames'))
            return frame

    def stop(self):
        with self._lock:
            self._requested = False
            self._last_frame = None
            camera = self._camera
            if camera is not None:
                camera.stop()
            self._running_expected = False

    def release(self):
        with self._lock:
            self._requested = False
            self._released = True
            self._last_frame = None
            self._release_inner()
