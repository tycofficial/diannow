"""Bounded pacing allowance for repairs that were actually transmitted.

The fixed video encoder rate describes original compressed media, not RTX.
Sharing that rate plus RTP overhead with repairs can throttle the primary
stream even on a path with spare capacity. This policy accounts for recent
repair wire bytes without inventing permanent traffic for hypothetical loss.
It cannot discover link capacity: the caller must provide fresh feedback and
withdraw the extra allowance when RTT / receiver queues indicate congestion.
No encoder rate, frame rate, packet contents or retransmission decision changes.
"""
import math
import time
from collections import deque


class RepairHeadroom:
    def __init__(self, *, clock=time.perf_counter, maximum_fraction=.25,
                 window_seconds=1.0, bucket_seconds=.1, hold_seconds=10.0):
        if not 0 < maximum_fraction <= .25:
            raise ValueError('Repair allowance must be positive and at most 25%')
        if not .1 <= window_seconds <= 5 or not .01 <= bucket_seconds <= window_seconds:
            raise ValueError('Invalid bounded observation window')
        if not 0 <= hold_seconds <= 30:
            raise ValueError('Invalid congestion hold')
        self.clock = clock
        self.maximum_fraction = maximum_fraction
        self.window = window_seconds
        self.bucket_seconds = bucket_seconds
        self.hold_seconds = hold_seconds
        self.buckets = deque()
        self.total_bytes = 0
        self.profile = None
        self.blocked_until = 0.0
        self.state = {'repairWireBitsPerSecond': 0, 'repairHeadroomBitsPerSecond': 0,
                      'repairHeadroomBlocked': False, 'repairObservationBuckets': 0}

    def _prune(self, now):
        while self.buckets and self.buckets[0][0] <= now - self.window:
            _, count = self.buckets.popleft()
            self.total_bytes -= count
        self.state['repairObservationBuckets'] = len(self.buckets)

    def record_repair(self, wire_bytes, *, now=None):
        """Call only after a repair packet successfully reaches the transport."""
        if not isinstance(wire_bytes, int) or not 0 < wire_bytes <= 65536:
            raise ValueError('Expected one positive RTP wire packet size')
        now = self.clock() if now is None else now
        self._prune(now)
        tick = math.floor(now / self.bucket_seconds) * self.bucket_seconds
        if self.buckets and self.buckets[-1][0] == tick:
            self.buckets[-1][1] += wire_bytes
        else:
            self.buckets.append([tick, wire_bytes])
        self.total_bytes += wire_bytes
        self.state['repairObservationBuckets'] = len(self.buckets)

    def budget(self, profile_bps, base_bps, *, congested=False,
               feedback_fresh=False, now=None):
        """Return the shared wire budget; stale / congested paths use base only.

        Samples expire after one second by default, with at most one bucket
        of conservative early expiry. After changing a manual video profile,
        old repairs cannot inflate its budget. The returned allowance is at
        most both measured repair wire traffic and 25% of the media profile.
        """
        if not all(isinstance(value, (int, float)) and math.isfinite(value) and value > 0
                   for value in (profile_bps, base_bps)):
            raise ValueError('Expected finite positive media and wire budgets')
        now = self.clock() if now is None else now
        if self.profile != profile_bps:
            self.profile = profile_bps
            self.buckets.clear()
            self.total_bytes = 0
        self._prune(now)
        if congested:
            self.blocked_until = max(self.blocked_until, now + self.hold_seconds)
        blocked = congested or not feedback_fresh or now < self.blocked_until
        measured = 8 * self.total_bytes / self.window
        allowance = 0 if blocked else min(measured, profile_bps * self.maximum_fraction)
        self.state.update(repairWireBitsPerSecond=round(measured),
                          repairHeadroomBitsPerSecond=round(allowance),
                          repairHeadroomBlocked=bool(blocked))
        return base_bps + allowance
