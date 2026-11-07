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

# A transient API failure on a stationary contact must still attempt UP.
events.clear();touch.active=True;touch.updated=False
def rejected_update(flags,pos):
    events.append((flags,pos))
    if flags==0x20006:raise OSError(87,'Rejected UPDATE')
touch.inject=rejected_update
touch.release(True)
assert [flags for flags,_ in events]==[0x20006,0x48000] and not touch.active

events.clear();touch.active=True;touch.updated=False
touch.release()
assert [flags for flags,_ in events]==[0x20006,0x40000] and not touch.active,'A successful UP must not trigger a duplicate mouse fallback for a rejected UPDATE'

events.clear();touch.active=True;touch.updated=True
def rejected_up(flags,pos):
    events.append((flags,pos))
    if flags==0x40000:raise OSError(87,'Rejected UP')
touch.inject=rejected_up
try:touch.release()
except OSError:pass
else:raise AssertionError('A failed UP must be reported after cancelling its contact')
assert [flags for flags,_ in events]==[0x40000,0x48000] and not touch.active
print('Native touch ABI, final-position update, UP coordinate invariants and cancellation passed without desktop input.')
