"""Reproduce WinError 87 without sending any input to the actual desktop."""
import remote_input as module
captured=[];module.emit=lambda values:captured.extend(values)
remote=module.RemoteInput();remote.bounds=(0,0,1920,1080);remote._touch_drag_target=lambda x,y:False
attempts=[]
def reject(event):attempts.append(event);raise OSError(87,'The parameter is incorrect')
remote.touch.apply=reject
def touch(phase,x=.4,y=.4,owner='phone'):remote.apply({'type':'touch','phase':phase,'x':x,'y':y,'_owner':owner})
touch('down');touch('up')
assert not remote.native_touch and len(attempts)==1
assert any(value.mi.dwFlags==2 for value in captured) and any(value.mi.dwFlags==4 for value in captured)
assert not remote.buttons and not remote.touch_owner
captured.clear();touch('down');touch('move',y=.6);touch('up',y=.6)
assert any(value.mi.dwFlags==0x800 for value in captured),'A single finger scroll must survive rejected native touch'
assert not any(value.mi.dwFlags==2 for value in captured),'Scrolling must not create an accidental click'
remote._touch_drag_target=lambda x,y:True;captured.clear();touch('down');touch('move',x=.5);touch('up',x=.5)
assert captured[1].mi.dwFlags==2 and captured[-1].mi.dwFlags==4,'Caption dragging starts immediately and releases'
touch('down');remote.release_owner('phone');assert not remote.buttons and not remote.compat_touch and 'phone' not in remote.owners
remote.touch.active=True;remote.touch.release=lambda cancel:(_ for _ in ()).throw(OSError(87,'Cancel rejected'))
remote.touch_owner='phone';remote.release_owner('phone');remote.disable()
assert not remote.touch_owner and not remote.buttons
print('Verified tap, scrolling, immediate caption dragging, cancel and disconnect after WinError 87. No desktop input was injected.')
