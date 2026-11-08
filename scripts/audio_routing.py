"""Independent desktop playback and microphone routing on Windows 10.

Core Audio COM vtables and Windows AudioPolicyConfig endpoint interop.
AudioPolicyConfig interface layout follows EarTrumpet (MIT), see THIRD_PARTY.md.
Physical-speaker mute and volume are never changed.
"""
import ctypes as c,os,threading,time,winreg
from ctypes import wintypes as w

class GUID(c.Structure):
    _fields_=[('data1',w.DWORD),('data2',w.WORD),('data3',w.WORD),('data4',c.c_ubyte*8)]
    def __init__(self,text):
        import uuid
        super().__init__();c.memmove(c.byref(self),uuid.UUID(text).bytes_le,16)

def call(pointer,slot,*types):
    address=c.cast(pointer,c.POINTER(c.POINTER(c.c_void_p))).contents[slot]
    function=c.WINFUNCTYPE(c.c_long,c.c_void_p,*types)(address)
    def invoke(*arguments):
        code=function(pointer,*arguments)
        if code<0:raise OSError(f'Core Audio HRESULT 0x{code&0xffffffff:08x}')
        return code
    return invoke

def release(pointer):
    if pointer:call(pointer,2)()

def endpoints(flow):
    result=[];direction='Render' if flow==0 else 'Capture'
    path=r'SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio'+'\\'+direction
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,path) as parent:
        for index in range(winreg.QueryInfoKey(parent)[0]):
            name=winreg.EnumKey(parent,index)
            try:
                with winreg.OpenKey(parent,name) as device:
                    if not winreg.QueryValueEx(device,'DeviceState')[0]&1:continue
                    with winreg.OpenKey(device,'Properties') as properties:
                        title=winreg.QueryValueEx(properties,'{a45c254e-df1c-4efd-8020-67d146a850e0},2')[0]
                result.append((str(title),'{0.0.'+('0' if flow==0 else '1')+'.00000000}.'+name))
            except OSError:continue
    return result

class CoreAudio:
    def __init__(self):
        self.ole=c.OleDLL('ole32');self.base=c.WinDLL('combase')
        self.enumerator=None;self.factory=None
        code=self.ole.CoInitializeEx(None,0)
        if code<0:raise OSError(f'CoInitializeEx 0x{code&0xffffffff:08x}')
        self.com_initialized=True
        try:self._initialize()
        except BaseException:self.close();raise
    def _initialize(self):
        self.enumerator=self.create('bcde0395-e52f-467c-8e3d-c4579291692e','a95664d2-9614-4f35-a746-de8db63617e6')
        self.base.WindowsCreateString.argtypes=[w.LPCWSTR,w.UINT,c.POINTER(c.c_void_p)]
        self.base.WindowsDeleteString.argtypes=[c.c_void_p]
        self.base.WindowsGetStringRawBuffer.argtypes=[c.c_void_p,c.POINTER(w.UINT)];self.base.WindowsGetStringRawBuffer.restype=w.LPCWSTR
        name=self.hstring('Windows.Media.Internal.AudioPolicyConfig');self.factory=c.c_void_p()
        self.base.RoGetActivationFactory.argtypes=[c.c_void_p,c.POINTER(GUID),c.POINTER(c.c_void_p)]
        try:
            code=self.base.RoGetActivationFactory(name,c.byref(GUID('ab3d4648-e242-459f-b02f-541c70306324')),c.byref(self.factory))
            if code<0:code=self.base.RoGetActivationFactory(name,c.byref(GUID('2a59116d-6c4f-45e0-a74f-707e3fef9258')),c.byref(self.factory))
            if code<0:raise OSError(f'Audio routing factory 0x{code&0xffffffff:08x}')
        finally:self.base.WindowsDeleteString(name)
    def create(self,cls,iid):
        pointer=c.c_void_p();code=self.ole.CoCreateInstance(c.byref(GUID(cls)),None,23,c.byref(GUID(iid)),c.byref(pointer))
        if code<0:raise OSError(f'CoCreateInstance 0x{code&0xffffffff:08x}')
        return pointer
    def hstring(self,text):
        result=c.c_void_p();code=self.base.WindowsCreateString(text,len(text),c.byref(result))
        if code<0:raise OSError('WindowsCreateString failed')
        return result
    def get_route(self,pid,role):
        value=c.c_void_p()
        try:call(self.factory,26,w.UINT,c.c_int,c.c_int,c.POINTER(c.c_void_p))(pid,0,role,c.byref(value))
        except OSError as error:
            if '80070057' in str(error):return ''  # no per-app override exists
            raise
        try:return self.base.WindowsGetStringRawBuffer(value,None) or ''
        finally:self.base.WindowsDeleteString(value)
    def set_route(self,pid,role,endpoint,packed=False):
        if endpoint and not packed:endpoint='\\\\?\\SWD#MMDEVAPI#'+endpoint+'#{e6327cad-dcec-4949-ae8a-991e976a79d2}'
        value=self.hstring(endpoint) if endpoint else c.c_void_p()
        try:call(self.factory,25,w.UINT,c.c_int,c.c_int,c.c_void_p)(pid,0,role,value)
        finally:self.base.WindowsDeleteString(value)
    def device(self,identifier):
        device=c.c_void_p();call(self.enumerator,5,w.LPCWSTR,c.POINTER(c.c_void_p))(identifier,c.byref(device));return device
    def default(self,flow,role):
        device=c.c_void_p();value=c.c_void_p()
        call(self.enumerator,4,c.c_int,c.c_int,c.POINTER(c.c_void_p))(flow,role,c.byref(device))
        try:
            call(device,5,c.POINTER(c.c_void_p))(c.byref(value));return c.wstring_at(value)
        finally:self.ole.CoTaskMemFree(value);release(device)
    def set_default(self,identifier,role):
        policy=self.create('870af99c-171d-4f9e-af0d-e63df40c2bc9','f8679f50-850a-41cf-9c72-430f290290c8')
        try:call(policy,13,w.LPCWSTR,c.c_int)(identifier,role)
        finally:release(policy)
    def configure_virtual_format(self,identifier):
        class WaveFormat(c.Structure):
            _pack_=1
            _fields_=[('tag',w.WORD),('channels',w.WORD),('rate',w.DWORD),('bytesPerSecond',w.DWORD),('blockAlign',w.WORD),('bits',w.WORD),('extra',w.WORD)]
        wave=WaveFormat(1,2,48000,192000,4,16,0)
        policy=self.create('870af99c-171d-4f9e-af0d-e63df40c2bc9','f8679f50-850a-41cf-9c72-430f290290c8')
        try:call(policy,6,w.LPCWSTR,c.c_void_p,c.c_void_p)(identifier,c.byref(wave),c.byref(wave))
        finally:release(policy)
    def activate(self,device,iid):
        result=c.c_void_p();call(device,3,c.POINTER(GUID),w.DWORD,c.c_void_p,c.POINTER(c.c_void_p))(c.byref(GUID(iid)),23,None,c.byref(result));return result
    def normalize_virtual(self,identifier):
        device=self.device(identifier);volume=None
        try:
            volume=self.activate(device,'5cdf2c82-841e-4546-9722-0cf74078229a')
            call(volume,7,c.c_float,c.POINTER(GUID))(1.0,None);call(volume,14,w.BOOL,c.POINTER(GUID))(False,None)
        finally:release(volume);release(device)
    def processes(self,identifier):
        device=self.device(identifier);manager=enumerator=None;result=set()
        try:
            manager=self.activate(device,'77aa99a0-1bd6-484f-8bc7-2c654c9a9b6f');enumerator=c.c_void_p()
            call(manager,5,c.POINTER(c.c_void_p))(c.byref(enumerator));count=w.UINT()
            call(enumerator,3,c.POINTER(w.UINT))(c.byref(count))
            for index in range(count.value):
                session=c.c_void_p();extended=c.c_void_p()
                try:
                    call(enumerator,4,c.c_int,c.POINTER(c.c_void_p))(index,c.byref(session))
                    call(session,0,c.POINTER(GUID),c.POINTER(c.c_void_p))(c.byref(GUID('bfb7ff88-7239-4fc9-8fa2-07c950be9c6d')),c.byref(extended))
                    pid=w.UINT();call(extended,14,c.POINTER(w.UINT))(c.byref(pid))
                    if pid.value:result.add(pid.value)
                except OSError:pass
                finally:release(extended);release(session)
        finally:release(enumerator);release(manager);release(device)
        return result
    def close(self):
        factory,self.factory=self.factory,None;enumerator,self.enumerator=self.enumerator,None
        initialized,self.com_initialized=getattr(self,'com_initialized',False),False
        try:release(factory)
        finally:
            try:release(enumerator)
            finally:
                if initialized:self.ole.CoUninitialize()

class MicrophoneRouting:
    def __init__(self):self.saved={};self.applied={}
    def apply(self,core,capture,mic,enabled):
        physical=next((identifier for name,identifier in capture if any(word in name.lower() for word in ['microphone','麦克风']) and not any(word in name.lower() for word in ['cable','virtual'])),None)
        # Repair defaults left pointing at a virtual input by older versions.
        if not self.saved and not enabled and physical:
            virtual={identifier for name,identifier in capture if 'cable' in name.lower()}
            for role in range(3):
                if core.default(1,role) in virtual:core.set_default(physical,role)
        if enabled and mic:
            if not self.saved:self.saved={role:core.default(1,role) for role in range(3)}
            # Retry partial policy failures and endpoint restarts. Previously
            # one failed role left saved nonempty and was never applied again.
            for role in range(3):
                if core.default(1,role)!=mic:core.set_default(mic,role)
                self.applied[role]=mic
        elif not enabled and self.saved:
            for role,identifier in list(self.saved.items()):
                if core.default(1,role)==self.applied.get(role):core.set_default(identifier,role)
                del self.saved[role];self.applied.pop(role,None)
    def restore(self,core):
        for role,identifier in self.saved.items():
            try:
                if core.default(1,role)==self.applied.get(role):core.set_default(identifier,role)
            except OSError:pass
        self.saved.clear();self.applied.clear()

class AudioRouter:
    def __init__(self):
        self.stop_event=threading.Event();self.wake_event=threading.Event();self.lock=threading.Lock();self.microphone_owners=set();self.thread=None;self.ready=False;self.microphone_ready=False;self.error=None
    def set_microphone(self,owner,enabled):
        with self.lock:
            if enabled:self.microphone_owners.add(owner)
            else:self.microphone_owners.discard(owner)
        self.wake_event.set()
    def start(self):
        if self.thread:return
        self.thread=threading.Thread(target=self.run,name='IndependentAudioRouting',daemon=True);self.thread.start()
    def run(self):
        core=None;saved={};routed=set();last_desktop=None;microphone=MicrophoneRouting();last_routes=0
        try:
            while not self.stop_event.is_set():
                try:
                    if core is None:core=CoreAudio()
                    render=endpoints(0);capture=endpoints(1)
                    desktop=next((identifier for name,identifier in render if 'hi-fi cable input' in name.lower()),None)
                    mic=next((identifier for name,identifier in capture if 'cable output' in name.lower() and 'hi-fi' not in name.lower()),None)
                    if desktop!=last_desktop:routed.clear();last_desktop=desktop
                    self.microphone_ready=bool(mic);self.ready=bool(desktop)
                    with self.lock:enabled=bool(self.microphone_owners)
                    microphone.apply(core,capture,mic,enabled)
                    if desktop and time.monotonic()-last_routes>=1:
                        last_routes=time.monotonic()
                        core.normalize_virtual(desktop)
                        for name,identifier in render:
                            try:processes=core.processes(identifier)
                            except OSError:continue  # one removed endpoint must not stop all routing
                            for pid in processes:
                                if pid==os.getpid() or pid in routed:continue
                                try:
                                    previous=saved.get(pid)
                                    if previous is None:previous={role:core.get_route(pid,role) for role in range(3)}
                                    # Save before changing roles, so a partial
                                    # failure remains restorable at shutdown.
                                    saved[pid]=previous
                                    for role in range(3):core.set_route(pid,role,desktop)
                                    routed.add(pid)
                                except OSError:continue
                    self.error=None
                except Exception as error:
                    # Devices can disappear during suspend/resume or the
                    # Windows Audio service restarting. Keep the worker alive.
                    self.error=type(error).__name__+': '+str(error);self.ready=False;self.microphone_ready=False
                    # An RPC-disconnected COM proxy remains broken forever.
                    # Recreate it on this same COM-owning routing thread while
                    # retaining original routes and microphone ownership.
                    if core:
                        try:core.close()
                        except Exception:pass
                    core=None;routed.clear();last_routes=0
                self.wake_event.wait(1);self.wake_event.clear()
        except Exception as error:self.error=str(error)
        finally:
            if core:
                for pid,previous in saved.items():
                    for role,value in previous.items():
                        try:core.set_route(pid,role,value,packed=True)
                        except OSError:pass
                microphone.restore(core)
                core.close()
    def close(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:self.thread.join(timeout=4)

if __name__=='__main__':
    import json
    core=CoreAudio();result=[]
    try:
        for flow in [0,1]:
            for name,identifier in endpoints(flow):
                if 'cable input' in name.lower() or 'cable output' in name.lower():
                    try:core.configure_virtual_format(identifier);result.append({'device':name,'rate':48000,'ok':True})
                    except OSError as error:result.append({'device':name,'ok':False,'error':str(error)})
        print(json.dumps(result))
    finally:core.close()
