"""Select a real DXGI desktop; never interpolate a 60Hz source into fake 120fps."""
import ctypes as c,json,os
from ctypes import wintypes as w
from pathlib import Path

STATE=Path(os.environ['LOCALAPPDATA'])/'CodexDesktopViewer'/'desktop-service'
class DISPLAY_DEVICE(c.Structure):
    _fields_=[('cb',w.DWORD),('DeviceName',w.WCHAR*32),('DeviceString',w.WCHAR*128),('StateFlags',w.DWORD),('DeviceID',w.WCHAR*128),('DeviceKey',w.WCHAR*128)]
class DEVMODE(c.Structure):
    _fields_=[('dmDeviceName',w.WCHAR*32),('dmSpecVersion',w.WORD),('dmDriverVersion',w.WORD),('dmSize',w.WORD),('dmDriverExtra',w.WORD),('dmFields',w.DWORD),('x',w.LONG),('y',w.LONG),('orientation',w.DWORD),('fixedOutput',w.DWORD),('color',c.c_short),('duplex',c.c_short),('yResolution',c.c_short),('ttOption',c.c_short),('collate',c.c_short),('formName',w.WCHAR*32),('logPixels',w.WORD),('bits',w.DWORD),('width',w.DWORD),('height',w.DWORD),('flags',w.DWORD),('frequency',w.DWORD),('icmMethod',w.DWORD),('icmIntent',w.DWORD),('mediaType',w.DWORD),('ditherType',w.DWORD),('reserved1',w.DWORD),('reserved2',w.DWORD),('panningWidth',w.DWORD),('panningHeight',w.DWORD)]

def displays():
    api=c.windll.user32;result=[];index=0
    while True:
        device=DISPLAY_DEVICE();device.cb=c.sizeof(device)
        if not api.EnumDisplayDevicesW(None,index,c.byref(device),0):break
        index+=1
        if device.StateFlags&8:continue
        mode=DEVMODE();mode.dmSize=c.sizeof(mode)
        if not api.EnumDisplaySettingsW(device.DeviceName,-1,c.byref(mode)) and not api.EnumDisplaySettingsW(device.DeviceName,-2,c.byref(mode)):continue
        virtual=any(word in device.DeviceString.lower() for word in ['virtual display','mttvdd'])
        result.append({'name':device.DeviceName,'title':device.DeviceString,'virtual':virtual,'primary':bool(device.StateFlags&4),'attached':bool(device.StateFlags&1),'bounds':[mode.x,mode.y,mode.width,mode.height],'refresh':mode.frequency})
    return result

def activate_virtual(width=1920,height=1080,refresh=120):
    if (width,height) not in [(1280,720),(1920,1080),(3840,2160)] or refresh not in [60,120]:raise ValueError('Unsupported virtual display mode')
    api=c.windll.user32;items=displays();target=next((d for d in items if d['virtual']),None)
    if not target:raise RuntimeError('The signed virtual display driver has not created a monitor yet.')
    mode=DEVMODE();mode.dmSize=c.sizeof(mode);index=0;found=False
    while api.EnumDisplaySettingsW(target['name'],index,c.byref(mode)):
        if mode.width==width and mode.height==height and mode.frequency==refresh:found=True;break
        index+=1
    if not found:raise RuntimeError('The virtual display does not advertise the requested mode.')
    mode.x=max((d['bounds'][0]+d['bounds'][2] for d in items if not d['virtual'] and d['attached']),default=1920);mode.y=0
    mode.dmFields|=0x20|0x40000|0x80000|0x100000|0x400000
    api.ChangeDisplaySettingsExW.argtypes=[w.LPCWSTR,c.POINTER(DEVMODE),w.HWND,w.DWORD,c.c_void_p]
    code=api.ChangeDisplaySettingsExW(target['name'],c.byref(mode),None,2,None)
    if code!=0:raise RuntimeError(f'Windows rejected the virtual 120Hz mode ({code}).')
    code=api.ChangeDisplaySettingsExW(target['name'],c.byref(mode),None,1,None)
    if code!=0:raise RuntimeError(f'Windows could not activate the virtual display ({code}).')
    set_preference('virtual');return displays()

def set_preference(mode):
    if mode not in ['original','virtual']:raise ValueError('Unknown display preference')
    STATE.mkdir(parents=True,exist_ok=True);path=STATE/'display-preference.json';temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps({'mode':mode}),encoding='utf-8');temporary.replace(path)

def capture_target():
    items=displays()
    try:preference=json.loads((STATE/'display-preference.json').read_text(encoding='utf-8')).get('mode')
    except (OSError,ValueError):preference='original'
    target=next((d for d in items if d['virtual'] and d['attached']),None) if preference=='virtual' else None
    target=target or next((d for d in items if d['primary']),None)
    if not target:raise RuntimeError('Windows does not expose an active desktop.')
    from dxcam.util.io import enum_dxgi_adapters,enum_dxgi_outputs
    from dxcam._libs.dxgi import DXGI_OUTPUT_DESC
    for device_index,adapter in enumerate(enum_dxgi_adapters()):
        for output_index,output in enumerate(enum_dxgi_outputs(adapter)):
            description=DXGI_OUTPUT_DESC();output.GetDesc(c.byref(description))
            if description.DeviceName==target['name']:
                return target,device_index,output_index
    raise RuntimeError('The selected monitor is not available to DXGI capture.')

if __name__=='__main__':
    import sys
    if '--activate-virtual' in sys.argv:print(json.dumps(activate_virtual()))
    else:print(json.dumps(displays()))
