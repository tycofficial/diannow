"""Bound the input backlog and prove reset cannot replay held contacts."""
import queue
import threading
import remote_input as module

pending=module.MotionInputQueue(maxsize=3)
def put(event):pending.put_latest((event,None))
for sequence in range(1,1001):put({'type':'move','seq':sequence,'x':sequence/1000,'_owner':'phone'})
assert pending.qsize()==1 and pending.queue[-1][0]['seq']==1000
put({'type':'move','seq':999,'x':.999,'_owner':'phone'})
assert pending.queue[-1][0]['seq']==1000,'Reordered motion cannot replace a newer unsent position'
put({'type':'button','down':True,'id':1,'_owner':'phone'})
put({'type':'move','seq':1001,'_owner':'phone'})
assert pending.qsize()==3,'A button boundary prevents motion from being merged across it'
put({'type':'move','seq':1002,'_owner':'phone'})
assert pending.qsize()==3 and pending.queue[-1][0]['seq']==1002
try:put({'type':'button','down':False,'id':2,'_owner':'phone'})
except queue.Full:pass
else:raise AssertionError('Reliable events must never be silently replaced')

pending=module.MotionInputQueue(maxsize=3)
for sequence in range(1,1001):pending.put_latest(({'type':'relative','totalX':sequence/10,'totalY':sequence/20,'seq':sequence},None))
assert pending.qsize()==1 and pending.get_nowait()[0]['totalX']==100
for sequence in range(1,1001):pending.put_latest(({'type':'touch','phase':'move','gesture':1,'touchSeq':sequence},None))
assert pending.qsize()==1 and pending.get_nowait()[0]['touchSeq']==1000

module.emit=lambda events:len(events)
remote=module.RemoteInput();remote.enabled=True;remote.events=module.MotionInputQueue(maxsize=2)
released=[];replies=[]
remote._release_all=lambda:released.append(True)
# Freeze worker dispatch while filling its bounded queue.
with remote.state_lock:
    for identifier in range(1,5):
        remote.submit({'type':'button','down':True,'id':identifier,'_owner':'phone'},lambda identifier,error:replies.append((identifier,error)))
    assert released and remote.input_epoch>0
    assert any(error and 'overflow' in error for _,error in replies)
    assert all(event.get('_inputEpoch')==remote.input_epoch for event,_ in remote.events.queue),'Overflow must remove every event from the old generation'

# An event already taken by the worker must also fail after owner disconnect.
complete=threading.Event();results=[]
def result(identifier,error):results.append((identifier,error));complete.set()
owner=module.RemoteInput();owner.enabled=True
with owner.state_lock:
    owner.submit({'type':'button','down':True,'id':99,'_owner':'departed'},result)
    owner.release_owner('departed')
assert complete.wait(1)
assert results[-1][0]==99 and 'released' in results[-1][1]
assert not owner.buttons
print('Input queue: 1000 moves coalesce to one, reliable boundaries survive, overflow purges stale state, disconnect fences queued input. No desktop input was injected.')
