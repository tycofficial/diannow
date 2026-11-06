"""Resolution and bitrate budgets for interactive desktop video."""

PROFILES={'480':(854,480,1500000),'720':(1280,720,4000000),'1080':(1920,1080,8000000),'2160':(3840,2160,24000000)}

def stream_dimensions(source_width,source_height,quality):
    maximum_width,maximum_height,_=PROFILES.get(str(quality),PROFILES['720'])
    scale=min(1,maximum_width/max(2,source_width),maximum_height/max(2,source_height))
    return max(2,int(source_width*scale)//2*2),max(2,int(source_height*scale)//2*2)

def stream_bitrate(width,height,fps):
    pixels=width*height
    budget=next((value[2] for value in PROFILES.values() if pixels<=value[0]*value[1]),24000000)
    return round(budget*max(.5,min(2,fps/60)))
