"""Verify latest-frame fanout without screen capture or network traffic."""
import concurrent.futures
import json
import threading
import time
import av
import numpy as np
from rtc_agent import SharedFrameSource,DesktopTrack,FastH264Encoder


class CameraDouble:
    is_capturing=True
    def __init__(self):self.grabs=0;self.image=np.random.default_rng(7).integers(0,256,(48,64,4),dtype=np.uint8)
    def get_latest_frame(self):self.grabs+=1;time.sleep(.003);return self.image.copy()
    def grab(self):raise AssertionError('Do not grab DXGI again while its capture thread is active.')


camera=CameraDouble();clock=[0.0];source=SharedFrameSource(camera,60,clock=lambda:clock[0])
tracks=[DesktopTrack(camera,64,48,60,'viewer-'+str(index),frame_source=source) for index in range(3)]
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:frames=list(pool.map(lambda track:track.read_frame(),tracks))
    assert camera.grabs==source.grabs==1 and source.conversions==1
    assert len({id(frame) for frame in frames})==3
    frames[0].opaque={'viewer':0};frames[0].pict_type=av.video.frame.PictureType.I;frames[0].pts=123
    assert frames[1].opaque['sessionId']=='viewer-1' and frames[1].pict_type!=av.video.frame.PictureType.I and frames[1].pts!=123
    original=frames[1].to_ndarray().copy()
    expected=av.VideoFrame.from_numpy_buffer(camera.image,format='bgra').reformat(width=64,height=48,format='yuv420p',threads=4,interpolation='BILINEAR').to_ndarray()
    assert np.array_equal(original,expected),'Sharing must preserve every converted pixel.'
    # Distinct resolutions reuse the raw snapshot, with a four-entry LRU bound.
    for width,height in [(32,24),(48,36),(40,30),(56,42)]:
        pixels,_,_,_=source.get_yuv(width,height)
        assert pixels.shape==(height*3//2,width)
    assert camera.grabs==1 and len(source.converted)==4
    # New tick replaces storage rather than overwriting data still encoded by
    # another viewer. A slow consumer holds no cache lock or frame queue.
    camera.image[:]=0;clock[0]=.020
    newer=tracks[2].read_frame()
    assert camera.grabs==2 and source.grabs==2 and len(source.converted)==1
    assert np.array_equal(frames[1].to_ndarray(),original)
    assert not np.array_equal(newer.to_ndarray(),original)
    # Real independent encoders may alter AVFrame fields, but cannot change
    # another consumer's metadata or the shared immutable pixel snapshot.
    encoder_frames=[track.read_frame() for track in tracks]
    before=encoder_frames[0].to_ndarray().copy()
    encoders=[FastH264Encoder() for _ in tracks]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        encoded=list(pool.map(lambda item:item[0].encode(item[1],True),zip(encoders,encoder_frames)))
    assert all(payload for payload,_ in encoded)
    assert all(np.array_equal(frame.to_ndarray(),before) for frame in encoder_frames)
    tracks[0].stop()
    assert not source.closed and tracks[1].read_frame().width==64
    source.close();assert not source.converted and source.raw is None
    assert np.array_equal(frames[1].to_ndarray(),original)
    try:source.get_yuv(64,48)
    except RuntimeError as error:assert 'closed' in str(error)
    else:raise AssertionError('Closed sources must reject new reads.')
    print(json.dumps({'threeViewersOneGrabOneConversion':True,'independentFrameMetadata':True,'pixelIdentical':True,'oldSnapshotSurvivesReplacementAndClose':True,'fourResolutionCacheLimit':True,'slowConsumersHoldNoQueue':True,'networkTraffic':False,'desktopCaptured':False}))
finally:
    for track in tracks:track.stop()
    source.close()
