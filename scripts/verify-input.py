import ctypes,time
import remote_input as module

captured=[];batches=[]
def record(inputs):captured.extend(inputs);batches.append((time.perf_counter(),list(inputs)))
module.emit=record
remote=module.RemoteInput()
remote.enabled=True
remote.apply({'type':'move','x':1,'y':0})
assert captured[-1].mi.dx==65535 and captured[-1].mi.dy==0
remote.apply({'type':'button','button':0,'down':True})
assert remote.buttons=={0}
remote.disable()
assert remote.buttons==set() and not remote.enabled
captured.clear();remote.apply({'type':'text','text':'中文A😀'})
assert len(captured)==10
assert [item.ki.dwFlags for item in captured]==[4,6]*5
captured.clear();remote.apply({'type':'key','key':'a','modifiers':['Control']})
assert [(item.ki.wScan,item.ki.dwFlags) for item in captured]==[(29,8),(30,8),(30,10),(29,10)]
assert batches[-1][0]-batches[-2][0]>=.035
captured.clear();remote.apply({'type':'key','key':'Escape'})
assert [(item.ki.wScan,item.ki.dwFlags) for item in captured]==[(1,8),(1,10)]
captured.clear();remote.apply({'type':'key','key':'ArrowLeft'})
assert [(item.ki.wScan,item.ki.dwFlags) for item in captured]==[(75,9),(75,11)]
assert ctypes.sizeof(module.INPUT)==40
print('Input checks passed: coordinates, release, Unicode, modifiers, Esc hold and extended scan codes. No desktop input was injected.')
