"""Check actual AMF NALs and fresh-decoder recovery after losing references."""
import fractions,time,json
import av
from rtc_agent import FastH264Encoder

encoder=FastH264Encoder();decoder=None;refreshes=[];recovered=[];switched=False
for index in range(80):
    frame=av.VideoFrame(640,360,'bgra')
    for plane in frame.planes:plane.update(bytes([index%256])*plane.buffer_size)
    frame.pts=index*1500;frame.time_base=fractions.Fraction(1,90000);frame.opaque={'fps':60}
    if index==40:
        encoder.target_bitrate=350000;encoder.created=time.monotonic()-1
    units=list(encoder._encode_frame(frame,index in [20,60]))
    kinds={unit[0]&31 for unit in units}
    if 5 in kinds:
        assert {7,8}.issubset(kinds),'Every decoder-refresh picture must include SPS/PPS'
        refreshes.append(index)
    if index in [15,55]:decoder=av.CodecContext.create('h264','r')
    if decoder is not None and units:
        try:
            frames=decoder.decode(av.Packet(b''.join(b'\x00\x00\x00\x01'+unit for unit in units)))
            if frames:recovered.append(index)
        except av.error.InvalidDataError:pass
    if index>=42 and getattr(encoder,'applied_bitrate',None)==350000:switched=True
assert any(20<=value<=22 for value in refreshes) and any(60<=value<=62 for value in refreshes)
assert any(20<=value<=22 for value in recovered) and any(60<=value<=62 for value in recovered)
assert encoder.codec.name=='h264_amf' and switched,'Hardware bitrate decrease must actually reopen at the lower configuration'
print(json.dumps({'hardwareEncoder':encoder.codec.name,'refreshFrames':refreshes,'freshDecoderRecoveryAt':[value for value in recovered if value in refreshes],'appliedBitrate':encoder.applied_bitrate,'desktopInputInjected':False}))
