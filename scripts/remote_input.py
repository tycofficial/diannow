"""Input used only by the authenticated remote viewer's WebRTC data channel."""
import ctypes, threading, queue, time
from ctypes import wintypes

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
KEYS={'Backspace':8,'Tab':9,'Enter':13,'Shift':16,'Control':17,'Alt':18,'Pause':19,'CapsLock':20,'Escape':27,'Space':32,'PageUp':33,'PageDown':34,'End':35,'Home':36,'ArrowLeft':37,'ArrowUp':38,'ArrowRight':39,'ArrowDown':40,'Insert':45,'Delete':46,'Meta':91}
KEYS.update({'F'+str(i):111+i for i in range(1,13)})
KEYS.update({chr(i):i for i in range(65,91)})
KEYS.update({str(i):48+i for i in range(10)})

def emit(inputs):
    if inputs:
        batch=(INPUT*len(inputs))(*inputs)
        if user32.SendInput(len(batch),batch,ctypes.sizeof(INPUT)) != len(batch):
            raise OSError('Windows did not accept remote input for this desktop.')
def key(vk,up=False):
    scan=user32.MapVirtualKeyW(vk,4)
    extended=(scan&0xff00) in [0xe000,0xe100] or vk in [33,34,35,36,37,38,39,40,45,46,91,92,111]
    return INPUT(type=1,ki=KEYBDINPUT(wVk=0,wScan=scan&0xff,dwFlags=8|(2 if up else 0)|(1 if extended else 0)))
def mouse(flags,data=0,x=0,y=0): return INPUT(type=0,mi=MOUSEINPUT(dx=x,dy=y,mouseData=data & 0xffffffff,dwFlags=flags))

class RemoteInput:
    def __init__(self):
        self.events=queue.Queue(maxsize=512);self.buttons=set();self.pressed=set();self.running=True;self.error=None;self.enabled=False;self.on_result=None;self.move_sequence=0
        self.thread=threading.Thread(target=self.run,name='RemoteInput',daemon=True);self.thread.start()
    def submit(self,event):
        if not self.enabled or not isinstance(event,dict):return
        try:self.events.put_nowait(event)
        except queue.Full:self.release_all()
    def release_all(self):
        flags={0:4,1:64,2:16}
        for button in list(self.buttons):emit([mouse(flags.get(button,4))])
        self.buttons.clear()
        if self.pressed:emit([key(vk,True) for vk in list(self.pressed)])
        self.pressed.clear()
    def disable(self):
        self.enabled=False
        while True:
            try:self.events.get_nowait()
            except queue.Empty:break
        self.release_all()
    def run(self):
        while self.running:
            event=self.events.get()
            try:
                if self.enabled:self.apply(event);self.error=None
                if self.on_result and event.get('id'):self.on_result(event['id'],None)
            except Exception as error:
                self.error=str(error)
                if self.on_result and event.get('id'):self.on_result(event['id'],self.error)
    def tap(self,vk,modifiers=()):
        down=list(dict.fromkeys([*modifiers,vk]))
        emit([key(value) for value in down]);self.pressed.update(down)
        try:time.sleep(.04)
        finally:
            emit([key(value,True) for value in reversed(down)])
            self.pressed.difference_update(down)
    def apply(self,event):
        kind=event.get('type')
        if kind=='move':
            sequence=event.get('seq',0)
            if sequence and sequence<self.move_sequence:return
            if sequence:self.move_sequence=sequence
            x=max(0,min(1,float(event.get('x',0))));y=max(0,min(1,float(event.get('y',0))))
            emit([mouse(0x8001,x=round(x*65535),y=round(y*65535))])
        elif kind=='button':
            if 'x' in event and 'y' in event:self.apply({'type':'move','x':event['x'],'y':event['y']})
            button=int(event.get('button',0));down=bool(event.get('down'))
            if button not in [0,1,2]:return
            emit([mouse({0:(2,4),1:(32,64),2:(8,16)}[button][0 if down else 1])])
            if down:self.buttons.add(button)
            else:self.buttons.discard(button)
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
            units=text.encode('utf-16-le');inputs=[]
            for offset in range(0,len(units),2):
                code=int.from_bytes(units[offset:offset+2],'little')
                inputs.extend([INPUT(type=1,ki=KEYBDINPUT(wScan=code,dwFlags=4)),INPUT(type=1,ki=KEYBDINPUT(wScan=code,dwFlags=6))])
            emit(inputs)
        elif kind=='key':
            name=str(event.get('key',''));vk=KEYS.get(name,KEYS.get(name.upper()))
            if vk is None:return
            modifiers=[KEYS[m] for m in event.get('modifiers',[]) if m in ['Control','Alt','Shift','Meta']]
            self.tap(vk,modifiers)
        elif kind=='release':self.release_all()
