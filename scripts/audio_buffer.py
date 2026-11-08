"""Small, bounded audio buffers. No device or asyncio dependency."""
from collections import deque
import threading


class LatestAudioFrames:
    """A producer never waits for playback or retains an old decode backlog."""
    def __init__(self, maximum=3):
        self.maximum=maximum;self.frames=deque();self.condition=threading.Condition();self.closed=False;self.dropped=0
    def put(self, frame):
        with self.condition:
            if self.closed:return False
            if len(self.frames)>=self.maximum:self.frames.popleft();self.dropped+=1
            self.frames.append(frame);self.condition.notify();return True
    def get(self, timeout):
        with self.condition:
            self.condition.wait_for(lambda:self.frames or self.closed,timeout)
            return self.frames.popleft() if self.frames else None
    def close(self):
        with self.condition:
            self.closed=True;self.frames.clear();self.condition.notify_all()


class PcmPlaybackBuffer:
    """Bound playback latency in bytes and satisfy every callback exactly."""
    def __init__(self, maximum_bytes, block_align):
        self.block_align=block_align;self.maximum=max(block_align,maximum_bytes//block_align*block_align)
        self.data=bytearray();self.lock=threading.Lock();self.dropped_bytes=0;self.underruns=0
    def put(self, data):
        if len(data)%self.block_align:raise ValueError('PCM must contain whole sample frames')
        with self.lock:
            overflow=max(0,len(self.data)+len(data)-self.maximum)
            if overflow:
                self.dropped_bytes+=overflow
                if overflow>=len(self.data):data=data[overflow-len(self.data):];self.data.clear()
                else:del self.data[:overflow]
            self.data.extend(data)
    def read(self, frame_count):
        count=frame_count*self.block_align
        with self.lock:
            result=bytes(self.data[:count]);del self.data[:len(result)]
            if len(result)<count:self.underruns+=1
        return result+bytes(count-len(result))
    def clear(self):
        with self.lock:self.data.clear()
