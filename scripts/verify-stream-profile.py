"""Synthetic profile/parallel-conversion checks; no capture, input or network."""
import concurrent.futures
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import av
import numpy as np
import rtc_agent as module
from stream_profile import stream_dimensions,stream_bitrate

for sw,sh in [(1920,1080),(3840,2160),(1080,1920),(640,360)]:
    w,h=stream_dimensions(sw,sh,'480')
    assert 0<w<=min(854,sw) and 0<h<=min(480,sh) and w%2==h%2==0
    assert abs(w/h-sw/sh)<.02
assert stream_dimensions(1920,1080,'720')==(1280,720)
assert stream_bitrate(852,480,60)==1500000
assert stream_bitrate(852,480,30)==750000

camera=SimpleNamespace(is_capturing=True)
source=module.SharedFrameSource(camera,hardware=True,clock=lambda:0)
raw=np.zeros((96,128,4),dtype=np.uint8)
source.snapshot=lambda after=None:(raw,0,1)
barrier=threading.Barrier(2)
def resize(image,size,interpolation):
    barrier.wait(timeout=2)
    return np.zeros((size[1],size[0],4),dtype=np.uint8)
try:
    with patch.object(module,'cv2',SimpleNamespace(resize=resize,INTER_LINEAR=1)):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda size:source.get_yuv(*size)[0],[(64,48),(32,24)]))
    assert [value.shape for value in results]==[(48,64,4),(24,32,4)]
    assert source.conversions==2
    # Same-resolution readers share the existing immutable conversion.
    same=[source.get_yuv(64,48)[0] for _ in range(3)]
    assert same[0] is same[1] is same[2] and source.conversions==2
finally:source.close()
print('Verified 480P aspect ratio, 1.5Mbps budget and parallel conversion without duplicate work.')
