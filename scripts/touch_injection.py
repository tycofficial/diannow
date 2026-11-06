"""Native Windows touch: lets the target app distinguish panning from dragging."""
import ctypes,threading,time
from ctypes import wintypes as w

class POINTER_INFO(ctypes.Structure):
    _fields_=[('pointerType',w.DWORD),('pointerId',w.UINT),('frameId',w.UINT),('pointerFlags',w.DWORD),('sourceDevice',w.HANDLE),('hwndTarget',w.HWND),('ptPixelLocation',w.POINT),('ptHimetricLocation',w.POINT),('ptPixelLocationRaw',w.POINT),('ptHimetricLocationRaw',w.POINT),('dwTime',w.DWORD),('historyCount',w.UINT),('InputData',ctypes.c_int32),('dwKeyStates',w.DWORD),('PerformanceCount',ctypes.c_uint64),('ButtonChangeType',ctypes.c_int)]
class POINTER_TOUCH_INFO(ctypes.Structure):
    _fields_=[('pointerInfo',POINTER_INFO),('touchFlags',w.DWORD),('touchMask',w.DWORD),('rcContact',w.RECT),('rcContactRaw',w.RECT),('orientation',w.UINT),('pressure',w.UINT)]

class NativeTouch:
    def __init__(self,api=None):
        self.api=api or ctypes.WinDLL('user32',use_last_error=True);self.active=False;self.updated=False;self.position=(0,0);self.ready=False;self.lock=threading.RLock();self.bounds=None
        self.api.InitializeTouchInjection.argtypes=[w.UINT,w.DWORD];self.api.InitializeTouchInjection.restype=w.BOOL
        self.api.InjectTouchInput.argtypes=[w.UINT,ctypes.POINTER(POINTER_TOUCH_INFO)];self.api.InjectTouchInput.restype=w.BOOL
    def inject(self,flags,position):
        contact=POINTER_TOUCH_INFO();p=contact.pointerInfo;p.pointerType=2;p.pointerId=1;p.pointerFlags=flags
        p.ptPixelLocation=w.POINT(*position);contact.touchMask=5;contact.rcContact=w.RECT(position[0]-2,position[1]-2,position[0]+2,position[1]+2);contact.pressure=512
        for attempt in range(3):
            if self.api.InjectTouchInput(1,ctypes.byref(contact)):return
            error=ctypes.get_last_error()
            if error!=21:raise ctypes.WinError(error)
            time.sleep(.001)
        raise ctypes.WinError(error)
    def apply(self,event):
        with self.lock:
            if not self.ready:
                if not self.api.InitializeTouchInjection(1,3):raise ctypes.WinError(ctypes.get_last_error())
                self.ready=True
            phase=event.get('phase');left,top,width,height=self.bounds or (0,0,self.api.GetSystemMetrics(0),self.api.GetSystemMetrics(1))
            position=(left+round(max(0,min(1,float(event.get('x',0))))*(width-1)),top+round(max(0,min(1,float(event.get('y',0))))*(height-1)))
            if phase=='down':
                if self.active:self.release(True)
                self.inject(0x10000|2|4,position);self.active=True;self.updated=False;self.position=position
            elif phase in ['move','up','cancel'] and self.active:
                if phase!='cancel' and position!=self.position:
                    self.inject(0x20000|2|4,position);self.position=position;self.updated=True
                if phase in ['up','cancel']:self.release(phase=='cancel')
    def release(self,cancel=False):
        with self.lock:
            if self.active:
                try:
                    # Windows requires UP to match a preceding UPDATE, even
                    # when a tap never moved. DOWN -> UP could fail with 87.
                    if not self.updated:self.inject(0x20000|2|4,self.position)
                    self.inject(0x40000|(0x8000 if cancel else 0),self.position)
                finally:self.active=False
