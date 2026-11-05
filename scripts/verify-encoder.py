import fractions
import av
from rtc_agent import FastH264Encoder,DeliveryPolicy
encoder=FastH264Encoder();policy=DeliveryPolicy(60)
first_codec=None;packets=0
for index in range(90):
    frame=av.VideoFrame(640,360,'yuv420p');frame.pts=index*1500;frame.time_base=fractions.Fraction(1,90000);frame.opaque={'fps':60,'delivery':policy}
    for plane in frame.planes:plane.update(bytes([128])*plane.buffer_size)
    encoder.target_bitrate=[15000000,750000,6000000][index%3]
    payload,timestamp=encoder.encode(frame,index==0);packets+=len(payload)
    if first_codec is None:first_codec=encoder.codec
    assert encoder.codec is first_codec,'Feedback recreated the encoder.'
assert encoder.codec.width==640 and encoder.codec.height==360
assert packets>0
print('Stable-quality check passed: 90 feedback changes kept one encoder and unchanged resolution.')
