"""Validate ABI and native touch state without injecting desktop input."""
import ctypes
from touch_injection import NativeTouch,POINTER_INFO,POINTER_TOUCH_INFO
assert ctypes.sizeof(POINTER_INFO)==96
assert ctypes.sizeof(POINTER_TOUCH_INFO)==144
touch=NativeTouch();events=[]
touch.inject=lambda flags,pos:events.append((flags,pos))
touch.ready=True
touch.apply({'phase':'down','x':.25,'y':.25});touch.apply({'phase':'up','x':.5,'y':.5})
assert [flags for flags,_ in events]==[0x10006,0x20006,0x40000]
assert events[-1][1]==events[-2][1] and not touch.active
touch.apply({'phase':'down','x':.3,'y':.3});touch.release(True)
assert events[-1][0]==0x48000 and not touch.active
events.clear();touch.apply({'phase':'down','x':.3,'y':.3});touch.apply({'phase':'up','x':.3,'y':.3})
assert [flags for flags,_ in events]==[0x10006,0x20006,0x40000],'A stationary tap must UPDATE before UP to avoid WinError 87'
print('Native touch ABI, final-position update, UP coordinate invariants and cancellation passed without desktop input.')
