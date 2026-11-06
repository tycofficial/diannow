"""Input used only by the authenticated remote viewer's WebRTC data channel."""
import ctypes, threading, queue, time,json
from ctypes import wintypes
from touch_injection import NativeTouch

user32=ctypes.WinDLL('user32',use_last_error=True)
ULONG_PTR=wintypes.WPARAM
class MOUSEINPUT(ctypes.Structure):
    _fields_=[('dx',wintypes.LONG),('dy',wintypes.LONG),('mouseData',wintypes.DWORD),('dwFlags',wintypes.DWORD),('time',wintypes.DWORD),('dwExtraInfo',ULONG_PTR)]
class KEYBDINPUT(ctypes.Structure):
    _fields_=[('wVk',wintypes.WORD),('wScan',wintypes.WORD),('dwFlags',wintypes.DWORD),('time',wintypes.DWORD),('dwExtraInfo',ULONG_PTR)]
class UNION(ctypes.Union): _fields_=[('mi',MOUSEINPUT),('ki',KEYBDINPUT)]
class INPUT(ctypes.Structure): _anonymous_=('u',);_fields_=[('type',wintypes.DWORD),('u',UNION)]
class POINT(ctypes.Structure):_fields_=[('x',wintypes.LONG),('y',wintypes.LONG)]
user32.SendInput.argtypes=[wintypes.UINT,ctypes.POINTER(INPUT),ctypes.c_int]
user32.SendInput.restype=wintypes.UINT
user32.MapVirtualKeyW.argtypes=[wintypes.UINT,wintypes.UINT]
user32.MapVirtualKeyW.restype=wintypes.UINT
user32.VkKeyScanW.argtypes=[wintypes.WCHAR]
user32.VkKeyScanW.restype=ctypes.c_short
user32.WindowFromPoint.argtypes=[POINT];user32.WindowFromPoint.restype=wintypes.HWND
user32.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];user32.GetAncestor.restype=wintypes.HWND
user32.SetForegroundWindow.argtypes=[wintypes.HWND];user32.SetForegroundWindow.restype=wintypes.BOOL
user32.GetForegroundWindow.restype=wintypes.HWND
user32.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
KEYS={'Backspace':8,'Tab':9,'Enter':13,'Shift':16,'Control':17,'Alt':18,'Pause':19,'CapsLock':20,'Escape':27,'Space':32,'PageUp':33,'PageDown':34,'End':35,'Home':36,'ArrowLeft':37,'ArrowUp':38,'ArrowRight':39,'ArrowDown':40,'Insert':45,'Delete':46,'Meta':91}
KEYS.update({'F'+str(i):111+i for i in range(1,13)})
KEYS.update({chr(i):i for i in range(65,91)})
KEYS.update({str(i):48+i for i in range(10)})
PHYSICAL={'ShiftLeft':160,'ShiftRight':161,'ControlLeft':162,'ControlRight':163,'AltLeft':164,'AltRight':165,'MetaLeft':91,'MetaRight':92,'Minus':189,'Equal':187,'BracketLeft':219,'BracketRight':221,'Backslash':220,'Semicolon':186,'Quote':222,'Comma':188,'Period':190,'Slash':191,'Backquote':192,'NumpadAdd':107,'NumpadSubtract':109,'NumpadMultiply':106,'NumpadDivide':111,'NumpadDecimal':110,'NumpadEnter':13}
PHYSICAL.update({'Key'+chr(i):i for i in range(65,91)})
PHYSICAL.update({'Digit'+str(i):48+i for i in range(10)})
PHYSICAL.update({'Numpad'+str(i):96+i for i in range(10)})
PHYSICAL['NumLock']=144

def process_integrity(pid):
    """Read a process's integrity without changing its token or permissions."""
    kernel32=ctypes.WinDLL('kernel32',use_last_error=True);advapi32=ctypes.WinDLL('advapi32',use_last_error=True)
    kernel32.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel32.OpenProcess.restype=wintypes.HANDLE
    kernel32.CloseHandle.argtypes=[wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes=[wintypes.HANDLE,wintypes.DWORD,ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetSidSubAuthorityCount.argtypes=[ctypes.c_void_p];advapi32.GetSidSubAuthorityCount.restype=ctypes.POINTER(ctypes.c_ubyte)
    advapi32.GetSidSubAuthority.argtypes=[ctypes.c_void_p,wintypes.DWORD];advapi32.GetSidSubAuthority.restype=ctypes.POINTER(wintypes.DWORD)
    process=kernel32.OpenProcess(0x1000,False,pid);token=wintypes.HANDLE()
    if not process:return None
    try:
        if not advapi32.OpenProcessToken(process,8,ctypes.byref(token)):return None
        length=wintypes.DWORD();advapi32.GetTokenInformation(token,25,None,0,ctypes.byref(length))
        if not length.value:return None
        buffer=ctypes.create_string_buffer(length.value)
        if not advapi32.GetTokenInformation(token,25,buffer,len(buffer),ctypes.byref(length)):return None
        sid=ctypes.cast(buffer,ctypes.POINTER(ctypes.c_void_p))[0]
        count=advapi32.GetSidSubAuthorityCount(sid)[0]
        return advapi32.GetSidSubAuthority(sid,count-1)[0]
    finally:
        if token:kernel32.CloseHandle(token)
        kernel32.CloseHandle(process)

def input_failure(accepted,total,error):
    message=f'Windows accepted {accepted}/{total} remote input events (Win32 error {error}).'
    try:
        import os
        foreground=wintypes.DWORD();user32.GetWindowThreadProcessId(user32.GetForegroundWindow(),ctypes.byref(foreground))
        own_level=process_integrity(os.getpid());target_level=process_integrity(foreground.value)
        if own_level is not None and target_level is not None and target_level>own_level:
            message+=' The foreground program has higher Windows privileges than the desktop agent.'
    except Exception:pass
    return message

def emit(inputs):
    if inputs:
        batch=(INPUT*len(inputs))(*inputs)
        ctypes.set_last_error(0)
        accepted=user32.SendInput(len(batch),batch,ctypes.sizeof(INPUT))
        error=ctypes.get_last_error()
        if accepted != len(batch):raise OSError(input_failure(accepted,len(batch),error))
def key(vk,up=False):
    scan=user32.MapVirtualKeyW(vk,4)
    extended=(scan&0xff00) in [0xe000,0xe100] or vk in [33,34,35,36,37,38,39,40,45,46,91,92,111]
    return INPUT(type=1,ki=KEYBDINPUT(wVk=0,wScan=scan&0xff,dwFlags=8|(2 if up else 0)|(1 if extended else 0)))
def mouse(flags,data=0,x=0,y=0): return INPUT(type=0,mi=MOUSEINPUT(dx=x,dy=y,mouseData=data & 0xffffffff,dwFlags=flags))

class RemoteInput:
    def __init__(self):
        self.touch=NativeTouch()
        self.native_touch=True;self.touch_error=None;self.compat_touch=None
        self.owners={};self.touch_owner=None;self.state_lock=threading.RLock()
        self.bounds=None
        self.events=queue.Queue(maxsize=512);self.buttons=set();self.pressed=set();self.running=True;self.error=None;self.enabled=False;self.on_result=None;self.move_sequence=0
        self.thread=threading.Thread(target=self.run,name='RemoteInput',daemon=True);self.thread.start()
    def submit(self,event,on_result=None):
        if not self.enabled or not isinstance(event,dict):return
        try:self.events.put_nowait((event,on_result or self.on_result))
        except queue.Full:self.release_all()
    def release_all(self):
        with self.state_lock:self._release_all()
    def _release_all(self):
        self._release_touch()
        self.touch_owner=None;self.owners.clear()
        flags={0:4,1:64,2:16}
        for button in list(self.buttons):emit([mouse(flags.get(button,4))])
        self.buttons.clear()
        if self.pressed:emit([key(vk,True) for vk in list(self.pressed)])
        self.pressed.clear()
    def release_owner(self,owner):
        with self.state_lock:
            if self.touch_owner==owner:self._release_touch();self.touch_owner=None
            state=self.owners.pop(owner,None)
            if not state:return
            for vk in state['keys']:
                if not any(vk in value['keys'] for value in self.owners.values()):emit([key(vk,True)]);self.pressed.discard(vk)
            for button in state['buttons']:
                if not any(button in value['buttons'] for value in self.owners.values()):emit([mouse({0:4,1:64,2:16}[button])]);self.buttons.discard(button)
    def disable(self):
        self.enabled=False;self.move_sequence=0;self.error=None
        while True:
            try:self.events.get_nowait()
            except queue.Empty:break
        self.release_all()
    def run(self):
        while self.running:
            event,on_result=self.events.get()
            try:
                if not self.enabled:raise OSError('The desktop input channel is not connected.')
                self.apply(event)
                # Pointer movement and an empty release do not prove that the
                # user's failed click or key reached its target program.
                if event.get('type') in ['button','wheel','key','text','touch','keyState']:self.error=None
                if on_result and event.get('id'):on_result(event['id'],None)
            except Exception as error:
                self.error=str(error)
                print(json.dumps({'remoteInputError':str(error),'eventType':event.get('type')}),flush=True)
                if on_result and event.get('id'):on_result(event['id'],self.error)
    def tap(self,vk,modifiers=()):
        down=list(dict.fromkeys([*modifiers,vk]))
        existing=set(self.pressed)
        down=[value for value in down if value not in existing]
        emit([key(value) for value in down]);self.pressed.update(down)
        try:time.sleep(.04)
        finally:
            emit([key(value,True) for value in reversed(down)])
            self.pressed.difference_update(down)
    def apply(self,event):
        with self.state_lock:self._apply(event)
    def _touch_failed(self,error):
        self.native_touch=False;self.touch_error=str(error);self.touch.active=False
        print(json.dumps({'touchFallback':True,'winerror':getattr(error,'winerror',None),'error':str(error)}),flush=True)
    def _release_touch(self):
        try:self.touch.release(True)
        except OSError as error:self._touch_failed(error)
        if self.compat_touch:
            owner=self.compat_touch['owner'];self.compat_touch=None
            self._apply({'type':'button','button':0,'down':False,'_owner':owner})
    def _touch_drag_target(self,x,y):
        if not self.bounds:return False
        left,top,width,height=self.bounds;point=POINT(left+round(x*(width-1)),top+round(y*(height-1)))
        target=user32.WindowFromPoint(point)
        if not target:return False
        target=user32.GetAncestor(target,2) or target;result=ULONG_PTR()
        user32.SendMessageTimeoutW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM,wintypes.UINT,wintypes.UINT,ctypes.POINTER(ULONG_PTR)]
        user32.SendMessageTimeoutW.restype=wintypes.LPARAM
        packed=(point.x&65535)|((point.y&65535)<<16)
        return bool(user32.SendMessageTimeoutW(target,0x84,0,packed,2,10,ctypes.byref(result))) and result.value in [2,10,11,12,13,14,15,16,17]
    def _compatible_touch(self,event,owner):
        phase=event.get('phase');x=max(0,min(1,float(event.get('x',0))));y=max(0,min(1,float(event.get('y',0))))
        def emit_event(kind,**values):self._apply({'type':kind,'_owner':owner,**values})
        if phase=='down':
            if self.compat_touch:self._release_touch()
            drag=self._touch_drag_target(x,y);self.compat_touch={'owner':owner,'start':(x,y),'last':(x,y),'drag':drag,'moved':False}
            emit_event('move',x=x,y=y)
            if drag:emit_event('button',button=0,down=True)
        elif self.compat_touch and self.compat_touch['owner']==owner:
            gesture=self.compat_touch;width,height=self.bounds[2:] if self.bounds else (1920,1080)
            if phase=='move':
                dx=(x-gesture['start'][0])*width;dy=(y-gesture['start'][1])*height
                if abs(dx)+abs(dy)>4:gesture['moved']=True
                if gesture['drag']:emit_event('move',x=x,y=y)
                elif gesture['moved']:
                    delta=round((y-gesture['last'][1])*height*2)
                    if delta:emit_event('wheel',delta=delta,x=x,y=y)
                gesture['last']=(x,y)
            elif phase in ['up','cancel']:
                self.compat_touch=None
                if gesture['drag']:emit_event('move',x=x,y=y);emit_event('button',button=0,down=False)
                elif phase=='up' and not gesture['moved']:
                    emit_event('button',button=0,down=True,x=x,y=y);emit_event('button',button=0,down=False)
    def cursor(self):
        point=POINT()
        if not user32.GetCursorPos(ctypes.byref(point)) or not self.bounds:return None
        left,top,width,height=self.bounds
        return {'x':max(0,min(1,(point.x-left)/max(1,width-1))),'y':max(0,min(1,(point.y-top)/max(1,height-1))),'visible':left<=point.x<left+width and top<=point.y<top+height}
    def _apply(self,event):
        kind=event.get('type')
        owner=event.get('_owner','legacy')
        state=self.owners.setdefault(owner,{'keys':set(),'buttons':set()})
        if kind=='touch':
            if event.get('phase')=='down':
                if self.touch_owner and self.touch_owner!=owner:self._release_touch()
                self.touch_owner=owner
            if self.touch_owner==owner:
                if self.native_touch:
                    try:self.touch.apply(event)
                    except OSError as error:
                        self._touch_failed(error)
                        self._compatible_touch({**event,'phase':'down'},owner)
                        if event.get('phase')!='down':self._compatible_touch(event,owner)
                else:self._compatible_touch(event,owner)
                if event.get('phase') in ['up','cancel']:self.touch_owner=None
        elif kind=='relative':
            previous=state.get('fraction',(0,0));x=max(-2000,min(2000,float(event.get('dx',0))))+previous[0];y=max(-2000,min(2000,float(event.get('dy',0))))+previous[1]
            dx=round(x);dy=round(y);state['fraction']=(x-dx,y-dy)
            if dx or dy:
                if self.bounds:
                    point=POINT();user32.GetCursorPos(ctypes.byref(point));left,top,width,height=self.bounds
                    x=point.x if left<=point.x<left+width else left+width/2;y=point.y if top<=point.y<top+height else top+height/2
                    self.apply({'type':'move','x':max(0,min(1,(x+dx-left)/max(1,width-1))),'y':max(0,min(1,(y+dy-top)/max(1,height-1))),'_owner':owner})
                else:emit([mouse(1,x=dx,y=dy)])
        elif kind=='keyState':
            code=str(event.get('code',''));vk=PHYSICAL.get(code,KEYS.get(code))
            if vk is None:return
            if event.get('down'):
                emit([key(vk)]);state['keys'].add(vk);self.pressed.add(vk)
            else:
                state['keys'].discard(vk)
                if not any(vk in value['keys'] for value in self.owners.values()):emit([key(vk,True)]);self.pressed.discard(vk)
        elif kind=='move':
            sequence=event.get('seq',0)
            if sequence and sequence<self.move_sequence:return
            if sequence:self.move_sequence=sequence
            x=max(0,min(1,float(event.get('x',0))));y=max(0,min(1,float(event.get('y',0))))
            if self.bounds:
                left,top,width,height=self.bounds;vx=user32.GetSystemMetrics(76);vy=user32.GetSystemMetrics(77);vw=user32.GetSystemMetrics(78);vh=user32.GetSystemMetrics(79)
                x=(left+x*(width-1)-vx)/max(1,vw-1);y=(top+y*(height-1)-vy)/max(1,vh-1)
            emit([mouse(0x8001|(0x4000 if self.bounds else 0),x=round(x*65535),y=round(y*65535))])
        elif kind=='button':
            if 'x' in event and 'y' in event:self.apply({'type':'move','x':event['x'],'y':event['y']})
            button=int(event.get('button',0));down=bool(event.get('down'))
            if button not in [0,1,2]:return
            if down:
                if button not in self.buttons:emit([mouse({0:2,1:32,2:8}[button])])
                state['buttons'].add(button);self.buttons.add(button)
            else:
                state['buttons'].discard(button)
                if not any(button in value['buttons'] for value in self.owners.values()):emit([mouse({0:4,1:64,2:16}[button])]);self.buttons.discard(button)
        elif kind=='wheel':
            if 'x' in event and 'y' in event:
                self.apply({'type':'move','x':event['x'],'y':event['y']})
                point=POINT();user32.GetCursorPos(ctypes.byref(point));target=user32.WindowFromPoint(point)
                if target:user32.SetForegroundWindow(user32.GetAncestor(target,2) or target)
            value=max(-1200,min(1200,int(event.get('delta',0))))
            emit([mouse(0x0800,value)])
        elif kind=='text':
            text=str(event.get('text',''))[:4096]
            if len(text)==1 and 32<=ord(text)<127:
                mapped=user32.VkKeyScanW(text)
                if mapped!=-1:
                    vk=mapped&255;bits=(mapped>>8)&255
                    if text.isalpha() and user32.GetKeyState(20)&1:bits^=1
                    self.tap(vk,[value for mask,value in [(1,16),(2,17),(4,18)] if bits&mask]);return
            # Unicode LF produces VK_PACKET rather than the Enter key that
            # Windows edit controls expect. Normalize line endings and keep
            # the whole text submission in one SendInput batch.
            units=text.replace('\r\n','\n').replace('\r','\n').encode('utf-16-le');inputs=[]
            for offset in range(0,len(units),2):
                code=int.from_bytes(units[offset:offset+2],'little')
                if code==10:
                    inputs.extend([key(KEYS['Enter']),key(KEYS['Enter'],True)]);continue
                inputs.extend([INPUT(type=1,ki=KEYBDINPUT(wScan=code,dwFlags=4)),INPUT(type=1,ki=KEYBDINPUT(wScan=code,dwFlags=6))])
            emit(inputs)
        elif kind=='key':
            name=str(event.get('key',''));vk=KEYS.get(name,KEYS.get(name.upper()))
            if vk is None:return
            modifiers=[KEYS[m] for m in event.get('modifiers',[]) if m in ['Control','Alt','Shift','Meta']]
            self.tap(vk,modifiers)
        elif kind=='release':self.release_owner(owner)
