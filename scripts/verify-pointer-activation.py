"""Opening/hovering a viewer cannot steal the physical computer's cursor."""
import ctypes
import json
from types import SimpleNamespace
from unittest.mock import patch
import remote_input as module

sent=[];touches=[]
def emit(inputs):
    sent.extend(inputs)
    return len(inputs)
def cursor(pointer):
    point=ctypes.cast(pointer,ctypes.POINTER(module.POINT)).contents
    point.x=100;point.y=200
    return True
api=SimpleNamespace(GetCursorPos=cursor,
                    GetSystemMetrics=lambda index:{76:0,77:0,78:3840,79:1080}.get(index,0),
                    WindowFromPoint=lambda point:0,
                    SetForegroundWindow=lambda window:(_ for _ in ()).throw(AssertionError('No actual foreground changes')))
touch=SimpleNamespace(active=False,bounds=None,apply=lambda event:touches.append(event),release=lambda cancel:None)
with patch.object(module,'emit',emit),patch.object(module,'user32',api),patch.object(module,'NativeTouch',lambda:touch):
    remote=module.RemoteInput();remote.bounds=(1920,0,1920,1080);remote.enabled=True
    def apply(kind,owner='phone',**values):remote.apply({'type':kind,'_owner':owner,**values})
    # The browser's connection release plus arbitrary hover never touches OS
    # input or initializes touch. The physical pointer is outside the VDD.
    apply('release')
    for seq in range(1,20):apply('move',x=.7,y=.3,seq=seq)
    apply('relative',totalX=0,totalY=0,seq=1)
    apply('wheel',delta=0,x=.9,y=.9)
    apply('button',button=0,down=False,x=.9,y=.9)
    assert not sent and not touches and not remote.owners['phone']['pointerArmed']

    # A deliberate click commits its own position, even after passive hover
    # carried a higher sequence. No phantom initial motion is required.
    apply('button',button=0,down=True,x=.2,y=.3,seq=1)
    assert len(sent)==2 and sent[-2].mi.dwFlags==0xC001 and sent[-1].mi.dwFlags==2
    assert remote.owners['phone']['pointerArmed'] and remote.buttons=={0}
    apply('move',x=.4,y=.5,seq=2)
    assert sent[-1].mi.dwFlags==0xC001
    apply('button',button=0,down=False,x=.6,y=.7,seq=3)
    assert sent[-2].mi.dwFlags==0xC001 and sent[-1].mi.dwFlags==4 and not remote.buttons
    count=len(sent);apply('move',x=.1,y=.1,seq=2)
    assert len(sent)==count,'Stale drag motion remains fenced'

    # Another connected viewer remains passive, and blur/disconnect revokes
    # activation without disturbing the cumulative trackpad baseline.
    apply('move',owner='ipad',x=.1,y=.1,seq=50)
    assert len(sent)==count and not remote.owners['ipad']['pointerArmed']
    apply('release');apply('move',x=.8,y=.8,seq=100)
    assert len(sent)==count and not remote.owners['phone']['pointerArmed']
    remote.release_owner('phone');apply('move',x=.2,y=.2,seq=101)
    assert len(sent)==count

    # Relative movement is itself a deliberate touchpad gesture. It can
    # activate and recenter onto the controlled display before a final click.
    apply('relative',totalX=10,totalY=5,seq=2)
    assert len(sent)==count+1 and remote.owners['phone']['pointerArmed']
    apply('button',button=0,down=True,relativeSnapshot={'totalX':15,'totalY':7,'seq':3})
    assert sent[-2].mi.dwFlags==0xC001 and sent[-1].mi.dwFlags==2
    apply('button',button=0,down=False);apply('release')
    count=len(sent);apply('relative',totalX=15,totalY=7,seq=3)
    assert len(sent)==count and not remote.owners['phone']['pointerArmed']

    # Wheel and native touch also activate before their coordinate-dependent
    # operations; neither requires an earlier absolute hover event.
    apply('wheel',owner='wheel',delta=120,x=.1,y=.2)
    assert sent[-2].mi.dwFlags==0xC001 and sent[-1].mi.dwFlags==0x800
    assert remote.owners['wheel']['pointerArmed']
    apply('touch',owner='touch',phase='down',x=.3,y=.4,gesture=1,touchSeq=1)
    assert touches[-1]['phase']=='down' and remote.owners['touch']['pointerArmed']
    apply('touch',owner='touch',phase='up',x=.3,y=.4,gesture=1,touchSeq=2)
    remote.disable()
    assert not remote.owners and not remote.buttons
print(json.dumps({'passiveViewerNeverMovesPhysicalCursor':True,'explicitPointerGesturesWork':True,
                  'buttonCoordinatesAndRelativeSnapshotAtomic':True,'releaseRevokesActivation':True,
                  'viewerActivationIsolated':True,'desktopInputInjected':False}))
