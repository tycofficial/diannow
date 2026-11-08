"""Select a real DXGI desktop; never interpolate a 60Hz source into fake 120fps."""
import ctypes as c,json,os,tempfile,threading
from ctypes import wintypes as w
from pathlib import Path

STATE=Path(os.environ['LOCALAPPDATA'])/'CodexDesktopViewer'/'desktop-service'
PREFERENCE_LOCK=threading.Lock()
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
    # Driver setup may call this to prepare a monitor. Selecting that monitor
    # for remote capture is a separate, explicit set_preference('virtual').
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
    return displays()

def set_preference(mode):
    if mode not in ['original','virtual']:raise ValueError('Unknown display preference')
    with PREFERENCE_LOCK:
        STATE.mkdir(parents=True,exist_ok=True);path=STATE/'display-preference.json';temporary=None
        try:
            # Concurrent viewers must not replace/delete each other's temporary
            # file. The final preference is still one complete atomic document.
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=STATE,prefix='display-preference-',suffix='.tmp',delete=False) as output:
                temporary=Path(output.name);json.dump({'mode':mode},output)
            temporary.replace(path)
        finally:
            if temporary:temporary.unlink(missing_ok=True)

def get_preference():
    with PREFERENCE_LOCK:
        try:value=json.loads((STATE/'display-preference.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):return 'original'
    return 'virtual' if isinstance(value,dict) and value.get('mode')=='virtual' else 'original'

def select_capture_display(items,preference='original'):
    active=[item for item in items if item['attached'] and item['bounds'][2]>0 and item['bounds'][3]>0]
    if preference=='virtual':
        target=next((item for item in active if item['virtual']),None)
        if target:return target
    # A virtual monitor can itself be marked primary by Windows. "Original"
    # means a physical desktop, so never fall back to an arbitrary primary.
    physical=[item for item in active if not item['virtual']]
    target=next((item for item in physical if item['primary']),None)
    if target:return target
    if physical:return physical[0]
    raise RuntimeError('Windows does not expose an attached physical desktop. Select virtual display explicitly to capture a virtual monitor.')

def capture_target():
    target=select_capture_display(displays(),get_preference())
    from dxcam.util.io import enum_dxgi_adapters,enum_dxgi_outputs
    from dxcam._libs.dxgi import DXGI_OUTPUT_DESC
    for device_index,adapter in enumerate(enum_dxgi_adapters()):
        for output_index,output in enumerate(enum_dxgi_outputs(adapter)):
            description=DXGI_OUTPUT_DESC();output.GetDesc(c.byref(description))
            if description.DeviceName==target['name'] and description.AttachedToDesktop:
                return target,device_index,output_index
    raise RuntimeError('The selected monitor is not available to DXGI capture.')

if __name__=='__main__':
    import sys
    if '--activate-virtual' in sys.argv:print(json.dumps(activate_virtual()))
    else:print(json.dumps(displays()))
