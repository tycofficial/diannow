"""Replay false low-REMB and actual congestion without media or network IO."""
from rtc_agent import DeliveryPolicy

policy=DeliveryPolicy(60);policy.bandwidth=350000
for second in range(1,30):
    policy.observe(32000,now=second)
    policy.receiver_feedback({'lossDelta':0,'bufferMs':30},now=second)
    assert policy.fps==60,'Low REMB alone must not throttle a healthy receiver.'
old_cap=max(5,min(60,int(350000*.8/(8*32000))))
assert old_cap==5

# A single bad report must not trigger a drop; repeated loss plus growing
# buffer does, while changes remain gradual and can never become stuck at 5.
policy.receiver_feedback({'lossDelta':4,'bufferMs':120},now=35)
assert policy.fps==60
policy.receiver_feedback({'lossDelta':5,'bufferMs':180},now=40)
assert policy.fps==48
for second in range(45,100,5):policy.receiver_feedback({'lossDelta':10,'bufferMs':300},now=second)
assert policy.fps==20
policy.receiver_feedback({'lossDelta':0,'bufferMs':35},now=101)
assert policy.fps==60,'A healthy receiver must recover despite stale low REMB.'

# Persistently high buffer is also evidence even without reported packet loss.
policy.receiver_feedback({'lossDelta':0,'bufferMs':300},now=110)
assert policy.fps==60
policy.receiver_feedback({'lossDelta':0,'bufferMs':310},now=115)
assert policy.fps==48
for second in range(121,160,6):policy.observe(32000,now=second)
assert policy.fps==60,'Missing feedback must permit bounded recovery probes.'

legacy=DeliveryPolicy(60);legacy.bandwidth=350000
for second in range(1,30):legacy.observe(64000,now=second)
assert legacy.fps==60,'Old clients start at target; an unverified estimate is not congestion.'
for malformed in [{'bufferMs':None},{'bufferMs':float('inf')},{'bufferMs':True}]:legacy.receiver_feedback(malformed,now=40)
assert legacy.fps==60
print('Replayed old 350kbps/32KB false cap: 5fps -> healthy 60fps. Confirmed congestion reduces gradually to 20fps and recovers; no media/network IO.')
