import ctypes,json,sys
from pathlib import Path
sys.path.insert(0,str(Path('scripts').resolve()))
from desktop_host import atomic_json,kernel,HEARTBEAT_NAME
path=Path('outputs/status-lock-test.json').resolve();assert path.parent==Path('outputs').resolve()
assert atomic_json(path,{'revision':1})
api=kernel();api.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p];api.CreateFileW.restype=ctypes.c_void_p
handle=api.CreateFileW(str(path),0x80000000,3,None,3,0x80,None)
assert handle and handle!=ctypes.c_void_p(-1).value
try:
 assert atomic_json(path,{'revision':2}) is False
 assert json.loads(path.read_text())['revision']==1
 event=api.CreateEventW(None,False,False,HEARTBEAT_NAME+'-test');signal=api.OpenEventW(2,False,HEARTBEAT_NAME+'-test');assert event and signal
 api.SetEvent(signal);assert api.WaitForSingleObject(event,0)==0;assert api.WaitForSingleObject(event,0)==258
 api.CloseHandle(signal);api.CloseHandle(event)
finally:api.CloseHandle(handle)
assert atomic_json(path,{'revision':3});assert json.loads(path.read_text())['revision']==3;path.unlink()
print('Real Windows read lock cannot crash status writes; independent kernel heartbeat remains observable while the file is locked.')
