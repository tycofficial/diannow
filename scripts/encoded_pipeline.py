"""One encoded video frame may prepare while the previous frame is sent.

The override belongs to one sender, never to aiortc's global sender class.
No second call to the same encoder starts before the first one has finished.
"""
import asyncio
import time
from aiortc import MediaStreamError


class EncodedPipeline:
    def __init__(self,sender,clock=time.perf_counter,maximum_age=.1):
        self.sender=sender;self.original=sender._next_encoded_frame
        self.clock=clock;self.maximum_age=maximum_age;self.pending=None;self.closed=False;self.tasks=set();self.invalidated=False
        self.stats={'prepared':0,'returned':0,'agedReturned':0,'staleDiscarded':0,'invalidatedDiscarded':0,'profileDiscarded':0,'keyframeRefreshes':0,'maximumPreparing':0}
        self.preparing=0

    def _observe(self,task):
        self.tasks.discard(task)
        if not task.cancelled():task.exception()

    async def _prepare(self,codec):
        self.preparing+=1;self.stats['maximumPreparing']=max(self.stats['maximumPreparing'],self.preparing)
        try:
            frame=await self.original(codec)
            encoder=getattr(self.sender,'_RTCRtpSender__encoder',None)
            self.stats['prepared']+=int(frame is not None)
            return frame,self.clock(),getattr(encoder,'applied_profile',None)
        finally:self.preparing-=1

    def _arm(self,codec):
        if self.closed:raise MediaStreamError
        if self.pending is None:
            self.pending=asyncio.create_task(self._prepare(codec))
            self.tasks.add(self.pending);self.pending.add_done_callback(self._observe)
        return self.pending

    async def _take(self,codec):
        task=self._arm(codec)
        try:return await task
        finally:
            if self.pending is task:self.pending=None

    def _profile_changed(self,profile):
        track=self.sender.track
        if profile is None or track is None:return False
        expected=(getattr(track,'width',None),getattr(track,'height',None),getattr(track,'fps',None),getattr(track,'fixed_profile',False))
        return profile!=expected

    def _refresh(self):
        # A discarded encoded P-frame may be referenced by the next P-frame.
        # Force a fresh IDR instead; merely skipping its packets breaks H264.
        encoder=getattr(self.sender,'_RTCRtpSender__encoder',None)
        if encoder is not None and hasattr(encoder,'last_keyframe_seconds'):encoder.last_keyframe_seconds=None
        self.sender._send_keyframe();self.stats['keyframeRefreshes']+=1

    def invalidate(self):
        """Request fresh pixels/IDR after an explicit playback resume."""
        if not self.closed:self.invalidated=True

    async def next_frame(self,codec):
        frame,prepared_at,profile=await self._take(codec)
        if self.closed:raise MediaStreamError
        if frame is not None:
            changed=self._profile_changed(profile)
            if changed or self.invalidated:
                self.stats['profileDiscarded' if changed else 'invalidatedDiscarded']+=1
                self.invalidated=False
                self._refresh()
                # One replacement only: do not build a frame queue or enter
                # an IDR loop while a slow transport is applying backpressure.
                frame,_,_=await self._take(codec)
                if self.closed:raise MediaStreamError
            elif self.clock()-prepared_at>self.maximum_age:
                # A slow IDR transmission can age its one pending P-frame.
                # Sending that frame preserves the reference chain; dropping
                # it by age would force another IDR and can create an IDR loop.
                # The next raw read already refreshes stale pixels upstream.
                self.stats['agedReturned']+=1
        self.stats['returned']+=int(frame is not None)
        self._arm(codec)
        return frame

    def close(self):
        if self.closed:return
        self.closed=True
        if self.pending is not None and not self.pending.done():self.pending.cancel()

    async def wait_closed(self):
        self.close()
        tasks=list(self.tasks)
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        # Already-completed tasks need not yield to their done callbacks when
        # awaited. Release payload references here as well as in _observe.
        self.tasks.difference_update(tasks);self.pending=None


def install_encoded_pipeline(sender,*,clock=time.perf_counter,maximum_age=.1):
    existing=getattr(sender,'_encoded_pipeline',None)
    if existing is not None:return existing
    pipeline=EncodedPipeline(sender,clock,maximum_age);sender._encoded_pipeline=pipeline
    sender._next_encoded_frame=pipeline.next_frame
    original_stop=sender.stop
    async def stop():
        pipeline.close()
        try:await original_stop()
        finally:await pipeline.wait_closed()
    sender.stop=stop
    track=sender.track
    if track is not None:
        track_stop=track.stop
        def stop_track():
            pipeline.close()
            return track_stop()
        track.stop=stop_track
    return pipeline
