"""Verify blocking device operations cannot stall the media event loop."""
import asyncio
import threading
import time
import json
from aiortc import MediaStreamTrack
from rtc_agent import MediaDevices
from rtc_agent import DesktopSession
from types import SimpleNamespace


class DeviceDouble:
    active=0;maximum=0;opened=[];closed=[]
    def __init__(self):
        type(self).active+=1;type(self).maximum=max(type(self).maximum,type(self).active)
        time.sleep(.04)
        type(self).active-=1;type(self).opened.append(self);self.close_count=0
    def close(self):
        time.sleep(.02);self.close_count+=1;type(self).closed.append(self)


class TrackDouble(MediaStreamTrack):
    kind='audio'
    def __init__(self):super().__init__();self.stops=0
    async def recv(self):raise AssertionError('No media reads in this test.')
    def stop(self):time.sleep(.03);self.stops+=1;super().stop()


async def main():
    devices=MediaDevices();ticks=[];running=True
    async def heartbeat():
        while running:ticks.append(time.perf_counter());await asyncio.sleep(.002)
    pulse=asyncio.create_task(heartbeat())
    try:
        opens=[asyncio.create_task(devices.open(DeviceDouble,'close')) for _ in range(3)]
        await asyncio.sleep(.01);opens[0].cancel()
        completed=await asyncio.gather(*opens,return_exceptions=True)
        assert isinstance(completed[0],asyncio.CancelledError)
        for resource in completed[1:]:await devices.call(resource.close)
        track=TrackDouble();devices.nonblocking_track_stop(track)
        started=time.perf_counter();track.stop();track.stop()
        assert time.perf_counter()-started<.015 and track.readyState=='ended'
        await devices.close_track(track)
        assert track.stops==1,'Implicit aiortc stop and explicit close must share teardown.'
        await devices.close()
        assert DeviceDouble.maximum==1,'Device constructors must never overlap.'
        assert len(DeviceDouble.opened)==len(DeviceDouble.closed)==3
        assert all(resource.close_count==1 for resource in DeviceDouble.opened)

        # Device-manager shutdown racing a live or cancelled constructor must
        # wait for and reclaim the object produced by that worker.
        for cancel in [False,True]:
            shutdown=MediaDevices();opening=asyncio.create_task(shutdown.open(DeviceDouble,'close'))
            await asyncio.sleep(.005)
            if cancel:opening.cancel()
            await shutdown.close();await asyncio.gather(opening,return_exceptions=True)
        assert len(DeviceDouble.opened)==len(DeviceDouble.closed)==5
        assert all(resource.close_count==1 for resource in DeviceDouble.opened)

        # An interrupted caller cannot interrupt the actual session cleanup.
        owner_devices=MediaDevices();device=await owner_devices.open(DeviceDouble,'close')
        owner=SimpleNamespace(camera=SimpleNamespace(is_capturing=False),input=None,source_width=1920,source_height=1080,refresh=60,media_devices=owner_devices,sessions=[],update_input_state=lambda:None)
        session=DesktopSession(owner);owner.sessions.append(session);session.mic_sink=device
        closing=asyncio.create_task(session.close());await asyncio.sleep(.005);closing.cancel()
        await asyncio.gather(closing,return_exceptions=True);await session.close()
        assert device.close_count==1 and not owner.sessions
        await owner_devices.close()
        gaps=[(b-a)*1000 for a,b in zip(ticks,ticks[1:])]
        assert max(gaps)<35,f'Blocking media lifecycle reached the event loop: {max(gaps):.1f}ms'
        print(json.dumps({'serializedDeviceConstruction':True,'cancelledOpenResourceReclaimed':True,'implicitTrackStopNonblocking':True,'eventLoopMaxGapMs':round(max(gaps),1),'desktopIO':False,'networkTraffic':False}))
    finally:
        running=False;await pulse
        if not devices.closed:await devices.close()


asyncio.run(main())
