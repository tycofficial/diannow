"""Exercise real Opus encoding after capture gaps; no audio devices or network."""
import fractions
import av,numpy as np
from aiortc.codecs.opus import OpusEncoder,OpusDecoder
from aiortc.jitterbuffer import JitterFrame
from desktop_audio import TimelineOpusEncoder

def frame(pts):
    result=av.AudioFrame.from_ndarray(np.zeros((1,1920),dtype=np.int16),format='s16',layout='stereo')
    result.sample_rate=48000;result.pts=pts;result.time_base=fractions.Fraction(1,48000)
    return result

timestamps=[0,960,1920,2880,3840,90240,91200,92160,93120,118080,119040]
old=OpusEncoder();old_times=[old.encode(frame(pts))[1] for pts in timestamps]
assert old_times[5]==4800 and old_times[6]==91200

encoder=TimelineOpusEncoder();decoder=OpusDecoder();last_codec=None;rebuilds=0;encoded_times=[]
for pts in timestamps:
    packets,stamp=encoder.encode(frame(pts))
    if last_codec is not None and encoder.codec is not last_codec:rebuilds+=1
    last_codec=encoder.codec
    assert len(packets)==1 and stamp==pts
    decoded=decoder.decode(JitterFrame(data=packets[0],timestamp=stamp))
    assert len(decoded)==1 and decoded[0].samples==960
    encoded_times.append(stamp)
assert rebuilds==2
assert all(first<second for first,second in zip(encoded_times,encoded_times[1:]))

# WASAPI endpoints can use 44.1 kHz, and a source can be mono. Preserve
# resampling's normal one-frame warmup, without emitting pre-gap audio or
# resetting the stream's original RTP origin at a gap.
for rate in [44100,48000]:
    for channels in [1,2]:
        encoder=TimelineOpusEncoder();decoder=OpusDecoder();samples=rate//50
        initial=rate//5;gap_time=fractions.Fraction(48,25)  # 1.92 s relative to stream start.
        previous_stamp=None;previous_codec=None;rebuilds=0;fresh_after_gap=[]
        for index in range(12):
            pts=initial+index*samples+(rate*18//10 if index>=6 else 0)
            source=av.AudioFrame.from_ndarray(np.zeros((1,samples*channels),dtype=np.int16),format='s16',layout='stereo' if channels==2 else 'mono')
            source.sample_rate=rate;source.pts=pts;source.time_base=fractions.Fraction(1,rate)
            packets,stamp=encoder.encode(source)
            if previous_codec is not None and encoder.codec is not previous_codec:rebuilds+=1
            previous_codec=encoder.codec
            if not packets:continue
            assert len(packets)==1 and (previous_stamp is None or stamp>previous_stamp)
            expected=round(fractions.Fraction(pts-initial,rate)*48000)
            assert 0<=expected-stamp<=960
            decoded=decoder.decode(JitterFrame(data=packets[0],timestamp=stamp))
            assert len(decoded)==1 and decoded[0].samples==960
            if index>=6:
                assert stamp>=int(gap_time*48000)
                fresh_after_gap.append(stamp)
            previous_stamp=stamp
        assert rebuilds==1 and len(fresh_after_gap)>=5
print('Audio timeline checks passed: reproduced stale Opus timestamp after a 1.8 s gap; fixed packets match capture time immediately, decode, and preserve normal continuous encoding. No devices or network used.')
