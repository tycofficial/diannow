"""Verify that a viewer alone never replaces the computer's microphone."""
from audio_routing import AudioRouter,MicrophoneRouting
class Core:
 def __init__(self,default='physical'):self.defaults={role:default for role in range(3)}
 def default(self,flow,role):return self.defaults[role]
 def set_default(self,identifier,role):self.defaults[role]=identifier
capture=[('Microphone Array','physical'),('CABLE Output','cable')]
core=Core();routing=MicrophoneRouting();router=AudioRouter()
routing.apply(core,capture,'cable',False);assert set(core.defaults.values())=={'physical'}
router.set_microphone('phone',True);router.set_microphone('ipad',True)
routing.apply(core,capture,'cable',bool(router.microphone_owners));assert set(core.defaults.values())=={'cable'}
router.set_microphone('phone',False)
routing.apply(core,capture,'cable',bool(router.microphone_owners));assert set(core.defaults.values())=={'cable'}
router.set_microphone('ipad',False)
routing.apply(core,capture,'cable',False);assert set(core.defaults.values())=={'physical'}
legacy=Core('cable');routing.apply(legacy,capture,'cable',False);assert set(legacy.defaults.values())=={'physical'}
print('Verified physical microphone preservation, remote activation, two-viewer ownership, restoration and legacy repair. No device changes.')
