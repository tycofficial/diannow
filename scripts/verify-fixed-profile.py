"""Fixed manual profiles ignore feedback without recreating the encoder."""
import fractions
import json
from types import SimpleNamespace
from unittest.mock import patch
import av
import rtc_agent as module
from stream_profile import stream_bitrate

policy=module.DeliveryPolicy(60,fixed=True)
for second in range(1,180):
    policy.bandwidth=350000
    policy.observe(64000,now=second)
    policy.receiver_feedback({'lossDelta':40,'bufferMs':1000,'viewerVisible':1},now=second)
    assert policy.fps==policy.max_fps==60 and policy.reason=='fixed-profile'
assert policy.last_feedback==179 and policy.last_buffer==1000
policy.receiver_feedback({'bufferMs':float('inf'),'viewerVisible':1},now=180)
policy.receiver_feedback({'bufferMs':10,'viewerVisible':0},now=181)
assert policy.last_feedback==179 and policy.last_buffer==1000

class CodecDouble:
    name='h264_amf'
    def __init__(self):self.bit_rate=0
    def open(self):pass
    def encode(self,frame):return []

created=[]
def create(name,mode):
    assert name=='h264_amf';value=CodecDouble();created.append(value);return value
encoder=module.FastH264Encoder()
def frame(index,fps=60,fixed=True,width=1920,height=1080):
    value=av.VideoFrame(width,height,'bgra');value.pts=index*1500;value.time_base=fractions.Fraction(1,90000);value.opaque={'fps':fps,'fixedProfile':fixed,'delivery':policy};return value
with patch.object(module.av,'CodecContext',SimpleNamespace(create=create)):
    for index in range(90):
        encoder.target_bitrate=[350000,40000000,750000][index%3]
        encoder.capacity_bitrate=350000
        list(encoder._encode_frame(frame(index),index==0))
        # Even a sustained downshift after the old minimum hold must not reopen.
        encoder.created=-1000
        encoder.rate_stability=SimpleNamespace(should_change=lambda *args:True)
        assert len(created)==1
        assert encoder.fixed_profile and encoder.target_bitrate==encoder.applied_bitrate==encoder.codec.bit_rate==8_000_000
        assert policy.bandwidth==8_000_000
    original=encoder.codec
    list(encoder._encode_frame(frame(90,fps=30),False))
    assert len(created)==2 and encoder.codec is not original and encoder.applied_bitrate==4_000_000
    list(encoder._encode_frame(frame(91,fps=30,fixed=False),False))
    assert len(created)==3 and not encoder.fixed_profile
    list(encoder._encode_frame(frame(92,fps=30,fixed=False,width=1280,height=720),False))
    assert len(created)==4 and encoder.codec.width==1280

print(json.dumps({'fixed1080Bitrate':8_000_000,'fixed60Fps':True,'feedbackChanges':90,'feedbackEncoderRecreates':0,'explicitProfileChangesRecreateOnce':True,'hardwareCallsMocked':True,'desktopCaptured':False,'networkTraffic':False}))
