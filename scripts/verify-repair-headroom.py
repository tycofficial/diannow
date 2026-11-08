"""Deterministic packet pacing with delayed NACKs; no network or capture IO.

This checks a software budget limit, not mobile network performance. The
constrained-link case explicitly retains congestion: adding pacing headroom
cannot make an insufficient path carry the fixed video profile.
"""
import asyncio
import heapq
import json
import math
import random

from repair_headroom import RepairHeadroom
from rtp_scheduling import PacketPacer


async def model(*, adaptive, loss, capacity, seconds=15):
    now = [0.0]
    async def sleep(delay):
        now[0] += delay
    profile = 8_000_000
    base = profile * 1.15
    policy = RepairHeadroom(clock=lambda: now[0])
    policy.budget(profile, base, feedback_fresh=True)
    pacer = PacketPacer(clock=lambda: now[0], sleep=sleep)
    rng = random.Random(7202)
    # Exact packet sizes for an 8 Mbps / 60 FPS constant-rate fixture. Include
    # 20 bytes of RTP/extensions plus the production 64 byte wire accounting.
    payload = round(profile / 8 / 60)
    sizes = [min(1180, payload-offset) + 84 for offset in range(0, payload, 1180)]
    repairs = []
    queue_report = []
    congestion = False
    link_ready = 0.0
    frames = repair_count = 0
    maximum_queue = 0.0
    maximum_headroom = 0.0
    samples = []

    async def send(size, repair=False):
        nonlocal link_ready, maximum_queue, maximum_headroom, congestion, repair_count
        while queue_report and queue_report[0][0] <= now[0]:
            _, congestion = heapq.heappop(queue_report)
        rate = policy.budget(profile, base, congested=congestion,
                             feedback_fresh=True) if adaptive else base
        maximum_headroom = max(maximum_headroom, rate-base)
        await pacer.wait(size, rate)
        link_ready = max(link_ready, now[0]) + 8*size/capacity
        queued = link_ready-now[0]
        maximum_queue = max(maximum_queue, queued)
        # The fixed 600 ms path is intentionally not called congestion. Only
        # queue growth is. Feedback itself arrives half a second later.
        heapq.heappush(queue_report, (now[0]+.5, queued > .075))
        if repair:
            policy.record_repair(size)
            repair_count += 1
        if rng.random() < loss:
            heapq.heappush(repairs, (link_ready+.6, size))

    while now[0] < seconds:
        due = frames/60
        if now[0] < due:
            now[0] = due
        for size in sizes:
            # One repair packet can interleave with each primary packet.
            # Its budget is shared, as in the production sender / worker.
            if repairs and repairs[0][0] <= now[0]:
                _, repair_size = heapq.heappop(repairs)
                await send(repair_size, True)
            await send(size)
        frames += 1
        if frames % 60 == 0:
            samples.append((now[0], policy.state['repairHeadroomBitsPerSecond']))
    return {'adaptive': adaptive, 'loss': loss, 'capacityMbps': capacity/1e6,
            'sentFramesPerSecond': frames/now[0], 'repairPackets': repair_count,
            'maximumQueueMs': maximum_queue*1000,
            'maximumHeadroomMbps': maximum_headroom/1e6,
            'finalHeadroomMbps': policy.state['repairHeadroomBitsPerSecond']/1e6,
            'samples': samples}


async def main():
    now = [1.0]
    policy = RepairHeadroom(clock=lambda: now[0])
    profile = 8_000_000
    base = profile*1.15
    assert policy.budget(profile, base, feedback_fresh=True) == base
    for _ in range(1000):
        policy.record_repair(1264)
    assert policy.budget(profile, base, feedback_fresh=True) == base+profile*.25
    assert policy.budget(profile, base) == base, 'Unknown feedback must not open headroom'
    assert policy.budget(profile, base, congested=True, feedback_fresh=True) == base
    now[0] += 1
    policy.record_repair(1264)
    assert policy.budget(profile, base, feedback_fresh=True) == base, 'Keep congestion hold'
    now[0] += 10
    assert policy.budget(profile, base, feedback_fresh=True) == base, 'Expire idle repair demand'
    policy.record_repair(1264)
    assert policy.budget(profile, base, feedback_fresh=True) == base+1264*8
    assert policy.budget(2_500_000, 2_875_000, feedback_fresh=True) == 2_875_000, 'Reset profile history'
    for i in range(2000):
        now[0] += .003
        policy.record_repair(1264)
        policy.budget(2_500_000, 2_875_000, feedback_fresh=True)
        assert len(policy.buckets) <= math.ceil(policy.window/policy.bucket_seconds)+1

    healthy_old = await model(adaptive=False, loss=0, capacity=20_000_000)
    healthy_new = await model(adaptive=True, loss=0, capacity=20_000_000)
    lossy_old = await model(adaptive=False, loss=.13, capacity=20_000_000)
    lossy_new = await model(adaptive=True, loss=.13, capacity=20_000_000)
    constrained = await model(adaptive=True, loss=.13, capacity=8_000_000)
    assert abs(healthy_old['sentFramesPerSecond']-healthy_new['sentFramesPerSecond']) < .001
    assert healthy_new['maximumHeadroomMbps'] == 0
    assert lossy_old['sentFramesPerSecond'] < 57
    assert lossy_new['sentFramesPerSecond'] >= 59.5
    assert lossy_new['sentFramesPerSecond'] > lossy_old['sentFramesPerSecond']+3
    assert lossy_new['maximumHeadroomMbps'] <= 2
    assert constrained['maximumQueueMs'] > 75, 'Model must actually create congestion'
    assert constrained['finalHeadroomMbps'] == 0, 'Withdraw measured allowance under congestion'
    assert constrained['sentFramesPerSecond'] < 58, 'Do not hide insufficient link capacity'
    for result in (healthy_old, healthy_new, lossy_old, lossy_new, constrained):
        result.pop('samples')
        print(json.dumps(result))
    print('Verified actual-repair-only headroom, fresh feedback requirement, 25% cap, congestion hold, expiry, bounded storage and profile reset. This is a deterministic software model, not real Internet throughput.')


asyncio.run(main())
