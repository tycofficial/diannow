"""Slow but connected video must fail the 60fps acceptance check."""
import copy
import json
from delivery_acceptance import delivery_failures

healthy = dict(quality='1080', dimensions=[1920, 1080], averageDecodedFps=59.8,
               minimumOneSecondFps=58.0, maximumFrameGapMs=24.0, zeroFpsIntervals=0)
assert not delivery_failures([healthy], True)
for changes in [dict(averageDecodedFps=12, minimumOneSecondFps=12),
                dict(minimumOneSecondFps=0, zeroFpsIntervals=1),
                dict(maximumFrameGapMs=1000), dict(maximumFrameGapMs=None),
                dict(averageDecodedFps=float('nan')), dict(dimensions=[854, 480])]:
    phase = copy.copy(healthy)
    phase.update(changes)
    assert delivery_failures([phase], True), changes
assert delivery_failures([healthy], False)
assert delivery_failures([], True)
print(json.dumps({'slowConnectedVideoRejected': True, 'freezesRejected': True,
                  'missingMetricsRejected': True, 'wrongResolutionRejected': True,
                  'networkTraffic': False, 'desktopCaptured': False}))
