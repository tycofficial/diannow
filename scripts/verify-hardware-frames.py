"""Verify raw-frame sharing and a working software fallback without capture."""
import fractions
from types import SimpleNamespace
from unittest.mock import patch
import av
import numpy as np
import rtc_agent as module

class Camera:
    is_capturing=True
    def __init__(self):self.pixels=np.zeros((48,64,4),dtype=np.uint8);self.reads=0
    def get_latest_frame(self):self.reads+=1;return self.pixels.copy()
    def grab(self):raise AssertionError('Duplicate DXGI capture')

camera=Camera();source=module.SharedFrameSource(camera,60,clock=lambda:0,hardware=True)
first=source.get_yuv(64,48)[0];second=source.get_yuv(64,48)[0]
assert first is second and first.shape==(48,64,4) and camera.reads==1 and source.conversions==0
small=source.get_yuv(32,24)[0];assert small.shape==(24,32,4) and camera.reads==1
original_context=av.CodecContext
attempts=[]
def create(name,*args,**kwargs):
    attempts.append(name)
    if name=='h264_amf':raise OSError('Simulated unavailable GPU')
    return original_context.create(name,*args,**kwargs)
encoder=module.FastH264Encoder()
with patch.object(module.av,'CodecContext',SimpleNamespace(create=create)):
    for index in range(3):
        frame=av.VideoFrame.from_numpy_buffer(first,format='bgra');frame.pts=index*1500;frame.time_base=fractions.Fraction(1,90000);frame.opaque={'fps':60}
        payload,_=encoder.encode(frame,index==0);assert payload
assert attempts.count('h264_amf')==1 and encoder.codec.name=='libx264'
assert not first.any(),'Encoding must not change the shared desktop pixels.'
source.close()
print('Native BGRA sharing, scaled BGRA caching, one capture per tick and automatic software fallback verified.')
