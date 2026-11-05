import ctypes,time,threading
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
captured.clear();batch_count=len(batches)
remote.apply({'type':'text','text':'甲\r\n😀\n乙\r丙'})
assert len(batches)==batch_count+1
assert len(captured)==16
assert [(item.ki.wScan,item.ki.dwFlags) for item in captured if item.ki.dwFlags in [8,10]]==[(28,8),(28,10)]*3
unicode_units=[item.ki.wScan for item in captured if item.ki.dwFlags==4]
assert unicode_units==[ord('甲'),0xd83d,0xde00,ord('乙'),ord('丙')]
assert all(item.ki.dwFlags in [4,6,8,10] for item in captured)
captured.clear();remote.apply({'type':'key','key':'a','modifiers':['Control']})
assert [(item.ki.wScan,item.ki.dwFlags) for item in captured]==[(29,8),(30,8),(30,10),(29,10)]
assert batches[-1][0]-batches[-2][0]>=.035
captured.clear();remote.apply({'type':'key','key':'Escape'})
assert [(item.ki.wScan,item.ki.dwFlags) for item in captured]==[(1,8),(1,10)]
captured.clear();remote.apply({'type':'key','key':'ArrowLeft'})
assert [(item.ki.wScan,item.ki.dwFlags) for item in captured]==[(75,9),(75,11)]
assert ctypes.sizeof(module.INPUT)==40

# Later no-op events must not erase a failed click before the viewer reads it.
complete=threading.Event();results=[]
def acknowledge(identifier,error):results.append((identifier,error));complete.set()
def fail_click(event):
    if event['type']=='button':raise OSError('mock click failed')
remote.apply=fail_click;remote.enabled=True
remote.submit({'type':'button','down':True,'id':1},acknowledge)
assert complete.wait(1);complete.clear()
assert remote.error=='mock click failed' and results[-1][1]=='mock click failed'
for kind in ['move','release']:
    remote.submit({'type':kind,'id':2},acknowledge)
    assert complete.wait(1);complete.clear()
    assert remote.error=='mock click failed'
remote.submit({'type':'key','id':3},acknowledge)
assert complete.wait(1);complete.clear()
assert remote.error is None and results[-1]==(3,None)

# A queue event that arrives after disconnect must not report a false success.
remote.enabled=False;remote.events.put(({'type':'key','id':4},acknowledge))
assert complete.wait(1)
assert results[-1][0]==4 and 'not connected' in results[-1][1]

original_process_integrity=module.process_integrity
module.process_integrity=lambda pid:12288 if pid!=__import__('os').getpid() else 8192
assert 'higher Windows privileges' in module.input_failure(0,1,0)
module.process_integrity=lambda pid:8192
assert 'higher Windows privileges' not in module.input_failure(0,1,5)
module.process_integrity=original_process_integrity
print('Input checks passed: coordinates, release, Unicode with multiline batches, modifiers, Esc hold, extended scan codes, persistent failure diagnostics and disconnected acknowledgements. No desktop input was injected.')
