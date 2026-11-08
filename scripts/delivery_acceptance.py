"""Honest acceptance criteria for real decoded video, independent of transport."""
import math


def delivery_failures(phases, connected, minimum_average=58.0,
                      minimum_interval=50.0, maximum_gap_ms=100.0):
    failures = []
    if not connected:
        failures.append('The media connection stopped')
    if not phases:
        failures.append('No video phase was measured')
    for phase in phases:
        prefix = str(phase.get('quality', '?')) + 'p: '
        def below(key, threshold):
            value = phase.get(key)
            return not isinstance(value, (int, float)) or not math.isfinite(value) or value < threshold
        if below('averageDecodedFps', minimum_average):
            failures.append(prefix + 'average decoded FPS below ' + str(minimum_average))
        if below('minimumOneSecondFps', minimum_interval):
            failures.append(prefix + 'one-second decoded FPS below ' + str(minimum_interval))
        gap = phase.get('maximumFrameGapMs')
        if not isinstance(gap, (int, float)) or not math.isfinite(gap) or gap > maximum_gap_ms:
            failures.append(prefix + 'decoded frame gap exceeds ' + str(maximum_gap_ms) + 'ms or is unknown')
        if phase.get('zeroFpsIntervals', 1) != 0:
            failures.append(prefix + 'a decoded video interval froze')
        dimensions = phase.get('dimensions')
        if not isinstance(dimensions, (list, tuple)) or len(dimensions) != 2 or any(not isinstance(d, (int, float)) or d <= 0 for d in dimensions):
            failures.append(prefix + 'decoded dimensions are missing')
        elif str(phase.get('quality')) in ('480', '720', '1080') and min(dimensions) != int(phase['quality']):
            failures.append(prefix + 'decoded resolution does not match the selected profile')
    return failures
