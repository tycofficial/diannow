"""Original-screen defaults and DXGI selection; no real display changes."""
import ctypes
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType,SimpleNamespace
from unittest.mock import patch

import display_target as display


def monitor(name,virtual=False,primary=False,attached=True):
    return {'name':name,'title':name,'virtual':virtual,'primary':primary,
            'attached':attached,'bounds':[0,0,1920,1080],'refresh':60}


physical=monitor('physical',primary=True)
secondary=monitor('secondary')
virtual=monitor('virtual',virtual=True,primary=True)
assert display.select_capture_display([virtual,physical]) is physical
assert display.select_capture_display([virtual,secondary],'original') is secondary
assert display.select_capture_display([virtual,physical],'broken') is physical
assert display.select_capture_display([virtual,physical],'virtual') is virtual
assert display.select_capture_display([{**virtual,'attached':False},physical],'virtual') is physical
assert display.select_capture_display([{**physical,'attached':False},secondary,virtual]) is secondary
for items in [[virtual],[{**physical,'attached':False},virtual],[{**physical,'bounds':[0,0,0,0]},virtual],[]]:
    try:display.select_capture_display(items)
    except RuntimeError as error:assert 'physical desktop' in str(error)
    else:raise AssertionError('Default must never capture a virtual monitor implicitly')

# Installing/preparing a virtual display must not silently select it. Only
# the explicit viewer command owns set_preference('virtual').
mode_calls=[]
def mode_settings(name,index,pointer):
    pointer._obj.width=1920;pointer._obj.height=1080;pointer._obj.frequency=120
    return 1
def change_settings(*args):mode_calls.append(args);return 0
api=SimpleNamespace(EnumDisplaySettingsW=mode_settings,ChangeDisplaySettingsExW=change_settings)
ctypes_stub=SimpleNamespace(windll=SimpleNamespace(user32=api),sizeof=ctypes.sizeof,
    byref=ctypes.byref,POINTER=ctypes.POINTER,c_void_p=ctypes.c_void_p)
with patch.object(display,'c',ctypes_stub),patch.object(display,'displays',lambda:[physical,virtual]),patch.object(display,'set_preference') as selected:
    display.activate_virtual()
    selected.assert_not_called()
assert len(mode_calls)==2


class Description(ctypes.Structure):
    _fields_=[('DeviceName',ctypes.c_wchar*32),('AttachedToDesktop',ctypes.c_bool)]


class Output:
    def __init__(self,name,attached=True):self.name=name;self.attached=attached
    def GetDesc(self,ptr):ptr._obj.DeviceName=self.name;ptr._obj.AttachedToDesktop=self.attached


io=ModuleType('dxcam.util.io');dxgi=ModuleType('dxcam._libs.dxgi')
io.enum_dxgi_adapters=lambda:['first','second']
io.enum_dxgi_outputs=lambda adapter:[Output('virtual'),Output('physical',False)] if adapter=='first' else [Output('physical')]
dxgi.DXGI_OUTPUT_DESC=Description
with TemporaryDirectory(prefix='display-target-test-') as directory,patch.object(display,'STATE',Path(directory)):
    path=Path(directory)/'display-preference.json'
    assert display.get_preference()=='original'
    for value in ['{broken','null','[]','42','"virtual"','{}','{"mode":"nonsense"}']:
        path.write_text(value,encoding='utf-8');assert display.get_preference()=='original'
    display.set_preference('original');assert display.get_preference()=='original'
    with patch.object(display,'displays',lambda:[virtual,physical]),patch.dict('sys.modules',{'dxcam.util.io':io,'dxcam._libs.dxgi':dxgi}):
        selected,adapter,output=display.capture_target()
        assert selected is physical and (adapter,output)==(1,0),'Skip detached duplicate DXGI outputs'
        display.set_preference('virtual')
        selected,adapter,output=display.capture_target()
        assert selected is virtual and (adapter,output)==(0,0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(display.set_preference,['original','virtual']*25))
    assert json.loads(path.read_text(encoding='utf-8'))['mode'] in ['original','virtual']
    assert not list(Path(directory).glob('*.tmp'))
    try:display.set_preference('other')
    except ValueError:pass
    else:raise AssertionError('Invalid preference must fail')

print('Physical original-screen default, virtual-primary avoidance, explicit virtual opt-in, disconnected display filtering, malformed preference recovery, atomic concurrent preferences and actual DXGI mapping logic passed. All display enumeration mocked; no real display preference or OS mode changed.')
