"""Reorder/drop motion without sending input to the actual desktop."""
from types import SimpleNamespace
import remote_input as module
captured=[];module.emit=lambda events:captured.extend(events)
remote=module.RemoteInput();remote.bounds=None
def relative(x,y,sequence):remote.apply({'type':'relative','totalX':x,'totalY':y,'seq':sequence,'_owner':'phone'})
relative(20,5,2);relative(10,2,1);relative(20,5,2)
remote.apply({'type':'button','button':0,'down':True,'relativeSnapshot':{'totalX':30,'totalY':8,'seq':3},'_owner':'phone'})
relative(30,8,3)
moves=[event.mi for event in captured if event.type==0 and event.mi.dwFlags==1]
assert sum(event.dx for event in moves)==30 and sum(event.dy for event in moves)==8
assert captured[-1].mi.dwFlags==2,'Final position is committed before button down'
remote.apply({'type':'button','button':0,'down':False,'_owner':'phone'})
remote.apply({'type':'release','_owner':'phone'})
relative(32,9,4)
moves=[event.mi for event in captured if event.type==0 and event.mi.dwFlags==1]
assert sum(event.dx for event in moves)==32 and sum(event.dy for event in moves)==9,'Blur must preserve cumulative baseline, without replaying old movement'
touches=[];remote.touch=SimpleNamespace(apply=lambda event:touches.append(event),release=lambda cancel:None,active=False)
def touch(phase,sequence,gesture=1):remote.apply({'type':'touch','phase':phase,'touchSeq':sequence,'gesture':gesture,'x':.4,'y':.4,'_owner':'phone'})
touch('down',1);touch('move',3);touch('move',2);touch('up',4);touch('move',5)
assert [event['phase'] for event in touches]==['down','move','up']
touch('down',1,2);touch('move',10,1);touch('up',2,2)
assert [event['gesture'] for event in touches]==[1,1,1,2,2]
remote.release_owner('phone');touch('move',100,2)
assert len(touches)==5 and not remote.touch_owner

# A reliable button boundary must fence older motion on the unreliable channel.
captured.clear()
remote.apply({'type':'button','button':0,'down':True,'_owner':'phone'})
remote.apply({'type':'move','x':.2,'y':.3,'seq':1,'_owner':'phone'})
remote.apply({'type':'button','button':0,'down':False,'x':.8,'y':.6,'seq':3,'_owner':'phone'})
before=len(captured)
remote.apply({'type':'move','x':.4,'y':.4,'seq':2,'_owner':'phone'})
assert len(captured)==before,'Late pre-release movement must not reposition the pointer'
remote.apply({'type':'button','button':2,'down':True,'_owner':'ipad'})
remote.apply({'type':'button','button':2,'down':False,'_owner':'ipad'})
before=len(captured)
remote.apply({'type':'move','x':.1,'y':.1,'seq':1,'_owner':'ipad'})
assert len(captured)==before+1,'A second viewer has an independent sequence counter'
assert 'legacy' not in remote.owners,'Button coordinates must retain their owning viewer'

# Fast motion can overtake a reliable DOWN; the click still belongs at DOWN's
# original coordinate, not wherever the overtaking packet moved the cursor.
captured.clear()
# Establish deliberate control before testing overtaken motion. Activation
# is per viewer, so a new owner's passive hover is intentionally ignored.
remote.apply({'type':'button','button':2,'down':True,'_owner':'reordered'})
remote.apply({'type':'button','button':2,'down':False,'_owner':'reordered'})
remote.apply({'type':'move','x':.8,'y':.8,'seq':2,'_owner':'reordered'})
remote.apply({'type':'button','button':0,'down':True,'x':.1,'y':.2,'seq':1,'_owner':'reordered'})
assert captured[-2].mi.dwFlags==0x8001 and captured[-2].mi.dx==round(.1*65535) and captured[-2].mi.dy==round(.2*65535)
assert captured[-1].mi.dwFlags==2,'Reliable DOWN must never be discarded with its older coordinate sequence'
before=len(captured)
remote.apply({'type':'move','x':.4,'y':.4,'seq':2,'_owner':'reordered'})
assert len(captured)==before,'Authoritative button coordinates must retain the highest motion fence'
remote.apply({'type':'move','x':.9,'y':.9,'seq':4,'_owner':'reordered'})
remote.apply({'type':'button','button':0,'down':False,'x':.3,'y':.4,'seq':3,'_owner':'reordered'})
assert captured[-2].mi.dx==round(.3*65535) and captured[-2].mi.dy==round(.4*65535) and captured[-1].mi.dwFlags==4
assert remote.owners['reordered']['moveSeq']==4 and not remote.buttons

# Failed native updates cancel any held Windows contact before mouse fallback.
cancelled=[]
remote.native_touch=True;remote.touch_owner='phone'
def failed_update(event):raise OSError(87,'Rejected touch UPDATE')
remote.touch=SimpleNamespace(apply=failed_update,release=lambda cancel:cancelled.append(cancel),active=True)
remote._touch_drag_target=lambda x,y:False
remote.apply({'type':'touch','phase':'down','_owner':'phone','x':.4,'y':.4})
assert cancelled==[True] and not remote.native_touch
print('Verified dropped relative packets recover before click and stale touch cannot revive a released contact. No desktop input was injected.')
