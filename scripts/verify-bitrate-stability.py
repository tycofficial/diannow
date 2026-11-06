from bitrate_stability import BitrateStability
gate=BitrateStability()
for now in range(1,100):
    value=350000 if now%6<3 else 6000000
    assert not gate.should_change(2000000,value,now,0),'Short bursts caused repeated hardware rebuilds'
gate=BitrateStability()
assert not gate.should_change(2000000,350000,1,0)
assert not gate.should_change(2000000,350000,29,0)
assert gate.should_change(2000000,350000,31,0)
gate=BitrateStability()
assert not gate.should_change(350000,1000000,31,30)
assert not gate.should_change(350000,1000000,49,30)
assert gate.should_change(350000,1000000,61,30)
print('Verified noisy REMB does not rebuild hardware repeatedly; persistent changes respect the 30-second hold and sustained trend.')
