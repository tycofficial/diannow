"""Model Windows timer overshoot without sending packets or waiting in real time."""
import asyncio
import json
from rtp_scheduling import PacketPacer


async def main():
    now=[0.0];sends=[];sleeps=[]
    async def oversleep(delay):
        assert delay>0
        sleeps.append(delay);now[0]+=delay+.002
    pacer=PacketPacer(clock=lambda:now[0],sleep=oversleep)
    for _ in range(128):
        await pacer.wait(125,1_000_000)
        sends.append(now[0]);now[0]+=.0001
        assert pacer.next_at>=now[0]-.0041,'Catch-up credit must remain bounded'
    elapsed=sends[-1]-sends[0];expected=127*.001
    assert expected-.004<=elapsed<=expected+.004,(elapsed,expected)
    # The old relative schedule adds 2ms of overshoot to every 1ms packet.
    assert elapsed<127*.003*.5,'Overshoot must not become permanent throughput loss'
    now[0]+=.5
    idle=now[0];burst=0
    for _ in range(12):
        await pacer.wait(125,1_000_000)
        if now[0]==idle:burst+=1
    assert burst<=5,'An idle connection cannot accumulate an unlimited burst'
    try:await pacer.wait(1,0)
    except ValueError:pass
    else:raise AssertionError('Reject invalid rates')
    print(json.dumps({'packets':128,'idealMs':round(expected*1000,1),'with2msTimerOvershootMs':round(elapsed*1000,1),'idleImmediatePackets':burst,'networkTraffic':False}))


asyncio.run(main())
