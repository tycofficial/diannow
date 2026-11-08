// Only declarative session state belongs here. Input actions must never be
// retransmitted by this application-level protocol.
export class SessionStateSync {
  constructor({writeState,writeLegacy,onTimeout=()=>{},setTimer=(fn,ms)=>setTimeout(fn,ms),clearTimer=id=>clearTimeout(id),maxAttempts=12}) {
    this.writeState=writeState;this.writeLegacy=writeLegacy;this.onTimeout=onTimeout;
    this.setTimer=setTimer;this.clearTimer=clearTimer;this.maxAttempts=maxAttempts;
    this.supported=false;this.revision=0;this.pending=new Map();this.desired=new Map();
  }
  accepts(event){return ['settings','videoState','audioState','microphoneState'].includes(event?.type)}
  send(event){
    if(!this.accepts(event))return false;
    const state=event.type==='settings'?{type:'settings',quality:String(event.quality),fps:Number(event.fps),fixedProfile:event.fixedProfile===true}:{type:event.type,enabled:event.enabled===true};
    const previous=this.desired.get(state.type);
    if(previous&&!previous.failed&&JSON.stringify(previous.state)===JSON.stringify(state))return this.supported||this.writeLegacy(state);
    if(previous?.timer!==undefined)this.clearTimer(previous.timer);
    const entry={state,revision:++this.revision,attempts:0};
    this.desired.set(state.type,entry);this.pending.set(state.type,entry);
    if(this.supported){this.transmit(entry);return true}
    return this.writeLegacy(state);
  }
  enable(){
    if(this.supported)return;
    this.supported=true;
    for(const entry of this.pending.values())this.transmit(entry);
  }
  transmit(entry){
    if(!this.supported||this.pending.get(entry.state.type)!==entry)return;
    entry.timer=undefined;
    if(entry.attempts>=this.maxAttempts){this.pending.delete(entry.state.type);entry.failed=true;this.onTimeout(entry.state);return}
    // Count failed writes as attempts too: a blocked or closed channel cannot
    // create an unbounded timer chain or build an application send queue.
    entry.attempts++;
    this.writeState({type:'sessionState',revision:entry.revision,state:entry.state});
    if(this.pending.get(entry.state.type)===entry){
      const delay=Math.min(1000,150*2**Math.min(3,entry.attempts-1));
      entry.timer=this.setTimer(()=>this.transmit(entry),delay);
    }
  }
  acknowledge(ack){
    if(ack?.type!=='stateAck'||!Number.isSafeInteger(ack.revision))return false;
    const entry=this.pending.get(ack.stateType);
    if(!entry||entry.revision!==ack.revision||ack.error)return false;
    if(ack.appliedRevision!==entry.revision)return false;
    if(entry.timer!==undefined)this.clearTimer(entry.timer);
    this.pending.delete(ack.stateType);return true;
  }
  reset(){
    for(const entry of this.pending.values())if(entry.timer!==undefined)this.clearTimer(entry.timer);
    this.pending.clear();this.desired.clear();this.supported=false;this.revision=0;
  }
}
