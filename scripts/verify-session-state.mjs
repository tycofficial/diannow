import assert from 'node:assert/strict';
import {SessionStateSync} from '../web/session-state.mjs';

function harness(){
  let clock=0,identifier=0;const timers=new Map(),wire=[],legacy=[],timeouts=[];
  const sync=new SessionStateSync({writeState:event=>{wire.push(event);return true},writeLegacy:event=>{legacy.push(event);return true},onTimeout:state=>timeouts.push(state),setTimer:(fn,delay)=>{timers.set(++identifier,{fn,at:clock+delay});return identifier},clearTimer:id=>timers.delete(id)});
  function advance(ms){const target=clock+ms;while(true){const next=[...timers].sort((a,b)=>a[1].at-b[1].at)[0];if(!next||next[1].at>target)break;clock=next[1].at;timers.delete(next[0]);next[1].fn()}clock=target}
  const ack=event=>sync.acknowledge({type:'stateAck',stateType:event.state.type,revision:event.revision,appliedRevision:event.revision});
  return {sync,wire,legacy,timers,timeouts,advance,ack};
}
const profile=quality=>({type:'settings',quality,fps:60,fixedProfile:true});
{
  const h=harness();h.sync.send(profile('480'));assert.equal(h.legacy.length,1);assert.equal(h.timers.size,0);
  h.sync.enable();assert.equal(h.wire.length,1);h.advance(1050);assert.equal(h.wire.length,4,'Dropped states/ACKs must be retried');
  const first=h.wire[0];h.sync.send(profile('1080'));const latest=h.wire.at(-1);assert(latest.revision>first.revision);assert.equal(h.timers.size,1,'Only the latest state retains a retry timer');
  assert.equal(h.ack(first),false);assert.equal(h.sync.pending.size,1,'Late ACK cannot clear a newer choice');
  assert.equal(h.ack(latest),true);assert.equal(h.timers.size,0);h.advance(20000);assert.equal(h.wire.at(-1),latest);
  assert.equal(h.legacy.length,1,'After negotiation, state must bypass the ordered input stream');
}
{
  const h=harness();h.sync.enable();
  for(const kind of ['audioState','microphoneState','videoState']){h.sync.send({type:kind,enabled:true});h.sync.send({type:kind,enabled:false})}
  h.sync.send(profile('1080'));
  assert.equal(h.sync.pending.size,4);assert.equal(h.timers.size,4);
  const old=h.wire[0];assert.equal(h.ack(old),false);
  h.advance(100000);assert.equal(h.timeouts.length,4);assert.equal(h.timers.size,0);assert.equal(h.sync.pending.size,0);
  for(const kind of ['audioState','microphoneState','videoState','settings'])assert.equal(h.wire.filter(e=>e.state.type===kind&&e.revision===h.wire.filter(e=>e.state.type===kind).at(-1).revision).length,12);
  h.sync.send({type:'videoState',enabled:false});assert.equal(h.timers.size,1,'A later explicit resync can recover after bounded timeout');
  h.sync.reset();h.advance(100000);assert.equal(h.timers.size,0);assert.equal(h.sync.desired.size,0);assert.equal(h.sync.supported,false);
}
{
  const h=harness();h.sync.enable();
  for(const type of ['key','keyState','text','button','touch','display','disconnect'])assert.equal(h.sync.send({type}),false);
  assert.equal(h.wire.length,0);assert.equal(h.timers.size,0,'Non-idempotent actions never enter the retry protocol');
  h.sync.send(profile('1080'));const sent=h.wire[0];
  assert.equal(h.sync.acknowledge({type:'stateAck',stateType:'settings',revision:sent.revision,appliedRevision:sent.revision,error:'temporary device failure'}),false);
  h.advance(150);assert.equal(h.wire.length,2);
}
console.log('Session state: bounded loss/ACK retry, latest wins, reset, action exclusion, and legacy fallback passed.');
