"""Real 5ms Opus packets, signal energy and timestamp gaps, without devices."""
import fractions,av,numpy as np
from aiortc.codecs.opus import OpusDecoder
from aiortc.jitterbuffer import JitterFrame
from desktop_audio import TimelineOpusEncoder
for rate in [44100,48000]:
    for channels in [1,2]:
        encoder=TimelineOpusEncoder();decoder=OpusDecoder();samples=rate//200;previous=None;count=0;levels=[];gap_stamp=None
        for index in range(200):
            pts=index*samples+(rate*18//10 if index>=100 else 0)
            mono=(np.sin(2*np.pi*440*(np.arange(samples)+pts)/rate)*12000).astype(np.int16)
            source=av.AudioFrame.from_ndarray(np.repeat(mono[:,None],channels,axis=1).reshape(1,-1),format='s16',layout='stereo' if channels==2 else 'mono');source.sample_rate=rate;source.pts=pts;source.time_base=fractions.Fraction(1,rate)
            packets,stamp=encoder.encode(source)
            assert len(packets)<=1,'No multi-packet batches may share an RTP timestamp'
            if not packets:continue
            assert previous is None or stamp>previous
            if index>=100:
                gap_stamp=gap_stamp or stamp
                assert stamp>=round((100*samples/rate+1.8)*48000)-240,'No stale pre-gap audio'
            for packet in packets:
                frames=decoder.decode(JitterFrame(data=packet,timestamp=stamp))
                assert len(frames)==1 and frames[0].samples==240
                levels.append(float(np.sqrt(np.mean(frames[0].to_ndarray().astype(np.float64)**2))));count+=1
            previous=stamp
        assert count>=196 and min(levels)>500
        print(rate,channels,count,round(min(levels)),gap_stamp)
print('5ms Opus timing, non-silent waveform and discontinuity recovery passed.')
