"""Verify virtual playback survives physical-speaker mute; restore initial state."""
import sys,threading,numpy as np,asyncio
from audio_routing import CoreAudio,call,c,w,release
core=CoreAudio()
from desktop_audio import DesktopAudioTrack
import pyaudiowpatch as pa
async def main():
 a=pa.PyAudio();device=next(d for d in a.get_device_info_generator() if 'Hi-Fi Cable Input' in d['name'] and d['maxOutputChannels']>0 and d['hostApi']==2);physical=core.default(0,0);dev=core.device(physical);vol=core.activate(dev,'5cdf2c82-841e-4546-9722-0cf74078229a');old=w.BOOL();call(vol,15,c.POINTER(w.BOOL))(c.byref(old));stop=threading.Event();rate=int(device['defaultSampleRate']);samples=rate//200;stream=a.open(format=pa.paInt16,channels=2,rate=rate,output=True,output_device_index=device['index'],frames_per_buffer=samples);tone=np.repeat((np.sin(2*np.pi*440*np.arange(samples)/rate)*12000).astype(np.int16)[:,None],2,axis=1).tobytes()
 def play():
  while not stop.is_set():stream.write(tone,exception_on_underflow=False)
 thread=threading.Thread(target=play);thread.start();track=DesktopAudioTrack();levels=[]
 try:
  await asyncio.sleep(1)
  for mute in [False,True]:
   call(vol,14,w.BOOL,c.c_void_p)(mute,None);rms=[]
   for i in range(60):
    frame=await track.recv()
    if i>=10:rms.append(float(np.sqrt(np.mean(frame.to_ndarray().astype(np.float64)**2))))
   levels.append(sum(rms)/len(rms))
  print({'virtualCapture':track.independent,'rate':track.rate,'unmutedRms':round(levels[0]),'physicalMutedRms':round(levels[1]),'ratio':round(levels[1]/max(1,levels[0]),3)})
  assert min(levels)>1000 and .9<levels[1]/levels[0]<1.1
 finally:
  call(vol,14,w.BOOL,c.c_void_p)(old.value,None);track.stop();stop.set();thread.join(2);stream.close();a.terminate();release(vol);release(dev);core.close()
asyncio.run(main())
