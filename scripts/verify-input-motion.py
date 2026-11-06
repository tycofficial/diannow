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
print('Verified dropped relative packets recover before click and stale touch cannot revive a released contact. No desktop input was injected.')
