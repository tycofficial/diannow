"""Hysteresis for hardware reconfiguration, separate from packet pacing."""
class BitrateStability:
    def __init__(self):self.direction=None;self.since=0
    def should_change(self,applied,requested,now,configured_at):
        direction='down' if requested<applied*.7 else 'up' if requested>applied*1.5 else None
        if direction!=self.direction:self.direction=direction;self.since=now
        if direction is None:return False
        # A momentary REMB change must not recreate the decoder every few
        # seconds. Pacing still follows instantaneous bandwidth meanwhile.
        return now-configured_at>=30 and now-self.since>=(4 if direction=='down' else 20)
