// Run the real in-browser sampler and phase acceptance with inert channels.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const source=await readFile(new URL('./verify-browser-video.mjs',import.meta.url),'utf8');
const expression=/sample=await evaluate\(`([\s\S]*?)`\)\}catch/.exec(source)?.[1].replace('${i%5===0}','true');
assert(expression,'The probe sampler must be inspectable without launching a browser');
let now=10000,sampleIndex=0;const sent=[];
const channel=readyState=>({readyState,send(message){assert.equal(this.readyState,'open');sent.push(JSON.parse(message))}});
const stats=()=>new Map([['video',{type:'inbound-rtp',kind:'video',timestamp:sampleIndex*1000,framesDecoded:sampleIndex*60,framesReceived:sampleIndex*60,frameWidth:1920,frameHeight:1080,packetsLost:sampleIndex,packetsReceived:sampleIndex*100,bytesReceived:sampleIndex*100000,jitterBufferDelay:sampleIndex*.6,jitterBufferEmittedCount:sampleIndex*60}]]);
const probe={channel:channel('closed'),stateChannel:channel('closed'),diagnosticChannel:channel('closed'),peer:{connectionState:'closed',getStats:async()=>stats()},lastReceiverReport:0};
const context=vm.createContext({probe,performance:{now:()=>now}});
sampleIndex=1;let sample=await vm.runInContext(expression,context);
assert.equal(sent.length,0);assert.equal(sample.controlState,'closed');assert.equal(sample.stateChannelState,'closed');assert.equal(sample.statsValid,true,'A closed channel must retain available receiver counters');
probe.peer.getStats=async()=>{throw new Error('Peer closed during statistics')};sample=await vm.runInContext(expression,context);assert.equal(sample.statsValid,false);assert.match(sample.statsError,/Peer closed/);
probe.peer.getStats=async()=>stats();probe.peer.connectionState='connected';probe.stateChannel.readyState='open';probe.channel.readyState='open';probe.previousInbound=null;
for(sampleIndex=1;sampleIndex<=4;sampleIndex++){now=10000+sampleIndex*1000;await vm.runInContext(expression,context)}
const reports=sent.filter(event=>event.receiverReport);assert.equal(reports.length,2);assert.equal(reports[1].time-reports[0].time,2000);assert.equal(reports[0].receiverReport.fps,60);assert.equal(reports[0].receiverReport.bufferMs,10);assert.equal(reports[0].receiverReport.lossDelta,1);

const acceptance=source.slice(source.indexOf('    const gaps=details.presented'),source.indexOf('    if(phaseError)break;'));
function phase(count,phaseError=null,virtual=false){
  const first={time:0,frames:0,width:1920,height:1080,freezeCount:0,freezeSeconds:0,bytes:0,lost:0,nack:0,pli:0};
  const last={...first,time:count*1000,frames:count*60,bytes:count*1000000,state:phaseError?'closed':'connected',controlState:phaseError?'closed':'open',stateChannelState:phaseError?'closed':'open'};
  const details={actualDisplay:{mode:virtual?'virtual':'original',virtual},presented:Array.from({length:count*60+1},(_,i)=>[i*1000/60,i]),rtts:[10],routineBytes:[500],source:[60],diagnostics:{}};
  return vm.runInNewContext(acceptance+';({result,failures})',{route:'direct',quality:'1080',seconds:30,applied:{elapsed:10,attempts:1},intervals:Array(count).fill(60),first,last,details,phaseError,failures:[],console:{log(){}}});
}
assert.equal(phase(30).result.acceptancePassed,true);
const interrupted=phase(25,'connection or control channel closed during measurement');
assert.equal(interrupted.result.acceptancePassed,false);assert.equal(interrupted.result.seconds,25);assert.equal(interrupted.result.completedIntervals,25);assert(interrupted.result.acceptanceFailures.some(value=>value.includes('incomplete measurement')));
assert.equal(phase(30,null,true).result.acceptancePassed,false,'A virtual wallpaper capture cannot pass the original-desktop probe');
console.log('Browser probe sampling: closed-channel safety, real 2s reports, retained partial results, incomplete-run failure, and physical-capture acceptance passed. No browser, network or input was used.');
