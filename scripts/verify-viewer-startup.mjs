import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const html=(await readFile('web/viewer.html','utf8')).replace('//__GESTURES__',(await readFile('web/gestures.mjs','utf8')).replace('export class','class'));
const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match=>match[1]);
assert.equal(scripts.length,2);
async function flush(){for(let i=0;i<60;i++)await Promise.resolve()}
async function start(rtcAvailable,sources=scripts,fetchResponse,AudioContextClass){
  const nodes={},stages=[],requests=[],peers=[],timers=new Map(),channels=[],sent=[],intervals=[];let timerId=0,clockNow=Date.now();
  for(const [,id] of html.matchAll(/id="([^"]+)"/g)){
    let open=false;
    const listeners={};
    nodes[id]={textContent:'',value:id==='quality'?'auto':id==='typingMode'?'live':id==='audioMode'?'low':id==='typing'?'':'60',options:[],listeners,muted:['screen','desktopAudio'].includes(id),playCalls:0,
      classList:{toggle(){open=!open;return open}},setAttribute(){},appendChild(){},addEventListener(type,listener){(listeners[type]??=[]).push(listener)},focus(){},blur(){},async play(){this.playCalls++;if(this.playFailure)throw new Error('Playback blocked')},setPointerCapture(){},getBoundingClientRect(){return {left:0,top:0,width:1280,height:720}}};
  }
  class Peer{
    iceGatheringState='complete';connectionState='new';
    remoteDescriptions=0;
    constructor(){peers.push(this)}
    createDataChannel(label){const channel={label,readyState:'open',bufferedAmount:0,send(value){if(channel.fail)throw new Error('Data channel failed');sent.push(JSON.parse(value))}};channels.push(channel);return channel}
    addTransceiver(){return {sender:{}}}
    async createOffer(){return {type:'offer',sdp:'test-sdp'}}
    async setLocalDescription(value){this.localDescription=value}
    async setRemoteDescription(){this.remoteDescriptions++;if(this.connectionState==='closed')throw new Error('Cannot answer a closed peer');this.connectionState='connected';this.onconnectionstatechange()}
    async getStats(){return this.stats||new Map()}
    close(){this.connectionState='closed';this.onconnectionstatechange?.()}
  }
  const window={addEventListener(){},isSecureContext:true,AudioContext:AudioContextClass};window.self=window;window.top=window;
  // WebKit rejects Window timer functions invoked as methods of TouchGestures.
  // Bare global calls and calls on Window remain valid.
  function wrongReceiver(value){return value&&Object.prototype.toString.call(value.pointers)==='[object Map]'}
  function setTimer(callback,delay){if(wrongReceiver(this))throw new TypeError('Can only call Window.setTimeout on instances of Window');timers.set(++timerId,{callback,delay});return timerId}
  function clearTimer(id){if(wrongReceiver(this))throw new TypeError('Can only call Window.clearTimeout on instances of Window');timers.delete(id)}
  const navigator={audioSession:{type:'auto'}};
  const context=vm.createContext({window,navigator,document:{getElementById:id=>nodes[id],querySelectorAll:()=>[],createElement:()=>({}),addEventListener(){}},
    location:{hostname:'screen.example'},setTimeout:setTimer,clearTimeout:clearTimer,setInterval(callback){intervals.push(callback)},requestAnimationFrame(){},performance:{now:()=>clockNow},
    Date:class extends Date{static now(){return clockNow}},AbortController,MediaStream:class{tracks=[];addTrack(track){this.tracks.push(track)}getTracks(){return this.tracks}getAudioTracks(){return this.tracks.filter(track=>track.kind==='audio')}},RTCPeerConnection:rtcAvailable?Peer:undefined,
    XMLHttpRequest:class{open(){}setRequestHeader(){}send(value){stages.push(JSON.parse(value).stage)}},
    fetch:async(path,options)=>{requests.push({path,options});return fetchResponse?await fetchResponse(path,options):{ok:true,status:200,json:async()=>({type:'answer',sdp:'answer-sdp'})}}});
  // Reproduce a host that already has common top-level names. Viewer must stay scoped.
  vm.runInContext('const video=1, keys=2, pc=3;',context);
  for(const script of sources)vm.runInContext(script,context);
  await flush();
  nodes.keyboard.onclick();
  assert.equal(nodes.keyboard.textContent,'收起键盘');
  if(stages.includes('connected')){
    const event={pointerId:1,pointerType:'touch',clientX:50,clientY:50,preventDefault(){}};
    nodes.screen.onpointerdown(event);nodes.screen.onpointerup(event);
  }
  async function fireTimer(delay){const entry=[...timers].find(([,timer])=>timer.delay===delay);assert(entry,'Expected a '+delay+' ms timer');timers.delete(entry[0]);entry[1].callback();await flush()}
  function dispatch(id,type,properties={}){const event={prevented:false,preventDefault(){this.prevented=true},...properties};for(const listener of nodes[id].listeners[type]||[])listener(event);return event}
  return {nodes,stages,requests,peers,timers,fireTimer,channels,sent,dispatch,intervals,navigator,advanceTime:ms=>clockNow+=ms};
}
const connected=await start(true);
assert(connected.stages.includes('connected'));
assert.equal(JSON.parse(connected.requests[0].options.body).preferences.quality,'720');
assert.equal(connected.requests[0].options.credentials,'omit');
const unavailable=await start(false);
assert(unavailable.stages.includes('connection-error'));
assert.match(unavailable.nodes.overlay.textContent,/没有提供/);
const broken=await start(true,scripts.map(source=>source.replace('setTimer=(callback,delay)=>setTimeout(callback,delay),clearTimer=timer=>clearTimeout(timer)','setTimer=setTimeout,clearTimer=clearTimeout')));
assert(!broken.stages.includes('connected'));
assert.match(broken.nodes.overlay.textContent,/Can only call Window.clearTimeout/);
const response=(status,value)=>({ok:status>=200&&status<300,status,json:async()=>value});
async function pendingAnswer(){
  let finish,offers=0;
  const pending=new Promise(resolve=>finish=resolve);
  const viewer=await start(true,scripts,async path=>{
    if(path==='/api/offer')return response(200,++offers===1?{id:'old-session'}:{type:'answer',sdp:'new-answer'});
    assert.equal(path,'/api/answer/old-session');return await pending;
  });
  assert.equal(viewer.peers.length,1);
  return {...viewer,finish};
}
for(const status of [503,200]){
  const viewer=await pendingAnswer();
  await viewer.fireTimer(30000);await viewer.fireTimer(3000);
  assert.match(viewer.nodes.status.textContent,/已连接/);
  assert.equal(viewer.peers.length,2);
  const state={status:viewer.nodes.status.textContent,overlay:viewer.nodes.overlay.textContent,stages:viewer.stages.length,timers:new Set(viewer.timers.keys())};
  viewer.finish(response(status,{type:'answer',sdp:'stale-answer'}));await flush();
  assert.equal(viewer.nodes.status.textContent,state.status,'A stale '+status+' answer must preserve the new connection status');
  assert.equal(viewer.nodes.overlay.textContent,state.overlay,'A stale answer must preserve the new connection overlay');
  assert.equal(viewer.stages.length,state.stages,'A stale answer must not emit connection stages');
  assert.equal(viewer.peers[0].remoteDescriptions,0,'A stale success must not answer the closed peer');
  assert(![...viewer.timers].some(([id,timer])=>!state.timers.has(id)&&[3000,5000].includes(timer.delay)),'A stale answer must not schedule another retry');
}
{
  const viewer=await pendingAnswer();
  await viewer.nodes.stop.onclick();
  const state={status:viewer.nodes.status.textContent,overlay:viewer.nodes.overlay.textContent,stages:viewer.stages.length,timers:new Set(viewer.timers.keys())};
  viewer.finish(response(200,{type:'answer',sdp:'stale-answer'}));await flush();
  assert.match(viewer.nodes.status.textContent,/已断开/);
  assert.equal(viewer.nodes.status.textContent,state.status,'A late answer must preserve intentional disconnect status');
  assert.equal(viewer.nodes.overlay.textContent,state.overlay);
  assert.equal(viewer.stages.length,state.stages);
  assert.equal(viewer.peers[0].remoteDescriptions,0,'A late answer must not reopen the disconnected peer');
  assert(![...viewer.timers].some(([id,timer])=>!state.timers.has(id)&&[3000,5000].includes(timer.delay)),'A late answer must not reconnect after intentional disconnect');
}
{
  const viewer=await start(true),{nodes,dispatch,sent}=viewer;
  const inputEvents=()=>sent.filter(event=>['text','key'].includes(event.type));
  assert.equal(nodes.typingMode.value,'live');
  nodes.typing.value='实时';dispatch('typing','input');
  assert.equal(inputEvents().at(-1).text,'实时');assert.equal(nodes.typing.value,'');
  dispatch('typing','compositionstart');nodes.typing.value='中文';dispatch('typing','input',{isComposing:true});
  const count=inputEvents().length;dispatch('typing','compositionend',{data:'中文'});
  assert.equal(inputEvents().length,count+1);assert.equal(inputEvents().at(-1).text,'中文');assert.equal(nodes.typing.value,'');
  nodes.typingMode.value='draft';nodes.typingMode.onchange();
  assert.equal(nodes.sendTyping.hidden,false);
  dispatch('typing','compositionstart');nodes.typing.value='语音输入第一行\n第二行';dispatch('typing','input',{isComposing:true});dispatch('typing','compositionend',{data:'第二行'});dispatch('typing','input');
  assert.equal(inputEvents().length,count+1,'Draft dictation must not send text');
  assert.equal(nodes.typing.value,'语音输入第一行\n第二行','Draft dictation must preserve textarea contents');
  assert.equal(dispatch('typing','keydown',{key:'Enter'}).prevented,false,'Draft Enter must allow a new line');
  assert.equal(dispatch('typing','beforeinput',{inputType:'deleteContentBackward'}).prevented,false,'Draft delete must edit locally');
  assert.equal(inputEvents().length,count+1,'Draft Enter and delete must not send remote keys');
  nodes.sendTyping.onclick();const first=inputEvents().at(-1);
  assert.equal(first.text,nodes.typing.value);assert.equal(nodes.sendTyping.disabled,true);assert(nodes.typing.value,'Sending must retain the draft until acknowledgement');
  viewer.channels.find(channel=>channel.label==='input').onmessage({data:JSON.stringify({type:'inputAck',id:first.id,error:'Input unavailable'})});
  assert.equal(nodes.typing.value,first.text,'A failed acknowledgement must preserve the draft');assert.equal(nodes.sendTyping.disabled,false);
  assert.equal(dispatch('typing','keydown',{key:'Enter',ctrlKey:true}).prevented,true);
  const retry=inputEvents().at(-1);viewer.channels.find(channel=>channel.label==='input').onmessage({data:JSON.stringify({type:'inputAck',id:retry.id})});
  assert.equal(nodes.typing.value,'','Only a successful acknowledgement clears the draft');assert.equal(nodes.typing.readOnly,false);
  const channel=viewer.channels.find(value=>value.label==='input');
  nodes.typing.value='保留草稿';channel.bufferedAmount=65536;nodes.sendTyping.onclick();
  assert.equal(nodes.typing.value,'保留草稿');assert.equal(nodes.sendTyping.disabled,false);
  channel.bufferedAmount=0;channel.fail=true;nodes.sendTyping.onclick();assert.equal(nodes.typing.value,'保留草稿');channel.fail=false;
  nodes.typing.value='a'.repeat(4097);const beforeOversized=inputEvents().length;nodes.sendTyping.onclick();assert.equal(inputEvents().length,beforeOversized,'Overlong text must not be silently truncated');
  nodes.typing.value='等待确认';nodes.sendTyping.onclick();await viewer.fireTimer(10000);
  assert.equal(nodes.typing.value,'等待确认','An acknowledgement timeout must preserve the draft');assert.equal(nodes.sendTyping.disabled,false);
  const beforeSwitch=inputEvents().length;nodes.typingMode.value='live';nodes.typingMode.onchange();
  assert.equal(nodes.typing.value,'');assert.equal(inputEvents().length,beforeSwitch,'Switching to live mode must not send the draft');
  nodes.typing.value='新';dispatch('typing','input');assert.equal(inputEvents().at(-1).text,'新');
  nodes.typingMode.value='draft';nodes.typingMode.onchange();assert.equal(nodes.typing.value,'等待确认','Returning to draft mode restores unsent text');
}
{
  const viewer=await start(false);viewer.nodes.typingMode.value='draft';viewer.nodes.typingMode.onchange();viewer.nodes.typing.value='离线草稿';viewer.nodes.sendTyping.onclick();
  assert.equal(viewer.nodes.typing.value,'离线草稿');assert.equal(viewer.sent.filter(event=>event.type==='text').length,0,'An offline draft must not be sent');
}
{
  const viewer=await start(true),{nodes,peers,channels}=viewer,peer=peers[0];
  const track=kind=>({kind,contentHint:'',stopped:false,stop(){this.stopped=true}}),videoTrack=track('video'),audioTrack=track('audio');
  const videoReceiver={jitterBufferTarget:60,playoutDelayHint:.05},audioReceiver={jitterBufferTarget:40,playoutDelayHint:.05};
  peer.ontrack({track:videoTrack,receiver:videoReceiver});peer.ontrack({track:audioTrack,receiver:audioReceiver});
  assert.deepEqual(nodes.screen.srcObject.getTracks(),[videoTrack],'The video player must receive only video');
  assert.deepEqual(nodes.desktopAudio.srcObject.getTracks(),[audioTrack],'Desktop audio must have its own player and stream');
  for(const receiver of [videoReceiver,audioReceiver]){assert.equal(receiver.jitterBufferTarget,0);assert.equal(receiver.playoutDelayHint,0)}
  assert.equal(videoTrack.contentHint,'detail');
  const unsupported={},otherVideo=track('video');peer.ontrack({track:otherVideo,receiver:unsupported});
  assert.equal(Object.hasOwn(unsupported,'jitterBufferTarget'),false,'An unsupported receiver hint must not be faked with an expando property');
  assert.equal(Object.hasOwn(unsupported,'playoutDelayHint'),false);
  const audioPlays=nodes.desktopAudio.playCalls;await nodes.audio.onclick();assert.equal(nodes.desktopAudio.playCalls,audioPlays,'An early sound click must wait for the audio channel');assert.equal(nodes.desktopAudio.muted,true);
  channels.find(channel=>channel.label==='pointer').onmessage({data:JSON.stringify({type:'pong',time:0,audio:true,fps:60})});
  await nodes.audio.onclick();assert.equal(nodes.desktopAudio.muted,false);assert.equal(nodes.screen.muted,true,'Sound must not unmute the video player');assert.equal(nodes.audio.textContent,'静音');
  await nodes.audio.onclick();assert.equal(nodes.desktopAudio.muted,true);
  nodes.desktopAudio.playFailure=true;await nodes.audio.onclick();assert.equal(nodes.desktopAudio.muted,true);assert.equal(nodes.audio.textContent,'开启声音');nodes.desktopAudio.playFailure=false;
  const stats=(timestamp,framesDecoded,jitterBufferDelay,jitterBufferEmittedCount)=>new Map([['video',{type:'inbound-rtp',kind:'video',timestamp,framesDecoded,bytesReceived:framesDecoded*100,jitterBufferDelay,jitterBufferEmittedCount,packetsLost:timestamp/1000,totalDecodeTime:framesDecoded*.004}]]);
  peer.stats=stats(1000,60,18,60);await viewer.intervals[0]();peer.stats=stats(2000,120,36,120);await viewer.intervals[0]();
  assert.match(nodes.stats.textContent,/缓冲 300 ms/,'The buffer statistic must remain the measured 300 ms dwell, not the requested zero hint');
  assert.match(nodes.stats.textContent,/接收 60 fps · 发送 60 fps/,'Receiver and sender frame rates must remain distinguishable');
  assert.equal(peers.length,1,'A high jitter buffer must not trigger reconnection');
  peer.stats=stats(3000,180,undefined,undefined);await viewer.intervals[0]();assert.match(nodes.stats.textContent,/缓冲 — ms/,'Unsupported delay statistics must not appear as zero');
  const reports=()=>viewer.sent.filter(event=>event.receiverReport);
  assert.equal(reports().length,1,'The prior valid measurement must ride the next ping');
  assert.deepEqual(reports()[0].receiverReport,{audioRms:null,audioEnabled:0,audioContextState:null,audioOutputMode:0,audioElementMuted:1,fps:60,bufferMs:300,lossDelta:1,decodeMs:4,mbps:.05,inputAckMs:null,inputAckAgeMs:null,audioBufferMs:null,audioTotalSamplesDuration:null,audioTotalSamplesReceived:null,audioConcealedSamplesDelta:null,audioPlayoutSamplesDuration:null,audioTotalPlayoutDelay:null,audioTotalSamplesCount:null,audioPlayoutMs:null,audioBaseLatencyMs:null,audioOutputLatencyMs:null,videoDecodedDelta:60,videoDroppedDelta:null,playbackFramesDelta:null,playbackDroppedDelta:null});
  await viewer.intervals[0]();assert.equal(reports().length,1,'Receiver reports must be throttled rather than sent every second');
  viewer.advanceTime(5000);await viewer.intervals[0]();assert.equal(reports().length,2);assert.equal(reports()[1].receiverReport.bufferMs,null,'Unsupported buffer statistics must be reported as unknown');
  const videoStream=nodes.screen.srcObject,audioStream=nodes.desktopAudio.srcObject;
  peer.connectionState='failed';peer.onconnectionstatechange();await viewer.fireTimer(3000);
  assert.equal(videoTrack.stopped,true);assert.equal(audioTrack.stopped,true);assert.equal(otherVideo.stopped,true);
  assert.notEqual(nodes.screen.srcObject,videoStream);assert.notEqual(nodes.desktopAudio.srcObject,audioStream);
  assert.equal(nodes.screen.srcObject.getTracks().length,0);assert.equal(nodes.desktopAudio.srcObject.getTracks().length,0);
  const currentVideo=nodes.screen.srcObject,currentAudio=nodes.desktopAudio.srcObject;
  peer.ontrack({track:track('audio'),receiver:audioReceiver});assert.equal(currentAudio.getTracks().length,0,'A stale track callback must not attach to the new stream');
  assert.equal(nodes.screen.srcObject,currentVideo);
  await nodes.stop.onclick();assert.equal(nodes.screen.srcObject,null);assert.equal(nodes.desktopAudio.srcObject,null);
}
{
  const viewer=await start(true),peer=viewer.peers[0],channel=viewer.channels.find(value=>value.label==='input');
  viewer.nodes.typing.value='测量';viewer.dispatch('typing','input');const input=viewer.sent.findLast(event=>event.type==='text');
  viewer.advanceTime(12);channel.onmessage({data:JSON.stringify({type:'inputAck',id:input.id})});
  let playback={totalVideoFrames:60,droppedVideoFrames:2};viewer.nodes.screen.getVideoPlaybackQuality=()=>playback;
  const richStats=second=>new Map([
    ['video',{id:'video',type:'inbound-rtp',kind:'video',timestamp:second*1000,framesDecoded:second*60,framesDropped:second===1?1:4,bytesReceived:second*100000,jitterBufferDelay:second*1.74,jitterBufferEmittedCount:second*60,totalDecodeTime:second*.24}],
    ['audio',{id:'audio',type:'inbound-rtp',kind:'audio',timestamp:second*1000,jitterBufferDelay:second*72000,jitterBufferEmittedCount:second*48000,totalSamplesDuration:second,totalAudioEnergy:second*.04,totalSamplesReceived:second*48000,concealedSamples:second===1?100:120}],
    ['playout',{id:'playout',type:'media-playout',kind:'audio',totalPlayoutDelay:second*9600,totalSamplesCount:second*48000,totalSamplesDuration:second}]
  ]);
  peer.stats=richStats(1);await viewer.intervals[0]();viewer.advanceTime(1000);playback={totalVideoFrames:120,droppedVideoFrames:7};peer.stats=richStats(2);await viewer.intervals[0]();assert.match(viewer.nodes.stats.textContent,/画面缓冲 29 ms · 声音缓冲 1500 ms/);viewer.advanceTime(1000);await viewer.intervals[0]();
  const report=viewer.sent.findLast(event=>event.receiverReport).receiverReport;
  assert.equal(report.bufferMs,29);assert.equal(report.audioBufferMs,1500,'Audio delay must be measured independently of the low video buffer');
  assert.equal(report.audioRms,.2,'Audio RMS must use actual received energy and duration deltas');
  assert.equal(report.inputAckMs,12,'Acknowledgement RTT must use the matching event send time');
  assert.equal(report.inputAckAgeMs,1000,'A delayed report must expose the age of the latest acknowledgement');
  assert.equal(report.audioTotalSamplesDuration,2);assert.equal(report.audioTotalSamplesReceived,96000);assert.equal(report.audioConcealedSamplesDelta,20);
  assert.equal(report.audioPlayoutSamplesDuration,2);assert.equal(report.audioTotalPlayoutDelay,19200);assert.equal(report.audioTotalSamplesCount,96000);assert.equal(report.audioPlayoutMs,200);
  assert.equal(report.videoDecodedDelta,60);assert.equal(report.videoDroppedDelta,3);assert.equal(report.playbackFramesDelta,60);assert.equal(report.playbackDroppedDelta,5);
  peer.stats=richStats(1);playback={totalVideoFrames:10,droppedVideoFrames:0};await viewer.intervals[0]();viewer.advanceTime(5000);await viewer.intervals[0]();
  const reset=viewer.sent.findLast(event=>event.receiverReport).receiverReport;
  for(const field of ['audioRms','audioBufferMs','audioConcealedSamplesDelta','audioPlayoutMs','videoDecodedDelta','videoDroppedDelta','playbackFramesDelta','playbackDroppedDelta'])assert.equal(reset[field],null,'A reset counter must not yield a fabricated '+field);
}
{
  const snapshot=(id,ssrc,timestamp,frames,delay=frames*.029)=>new Map([['video',{id,ssrc,type:'inbound-rtp',kind:'video',timestamp,framesDecoded:frames,bytesReceived:frames*1000,jitterBufferDelay:delay,jitterBufferEmittedCount:frames}]]);
  for(const value of [snapshot('next',1,3000,180),snapshot('first',2,3000,180),snapshot('first',1,2000,180),snapshot('first',1,1500,180)]){
    const viewer=await start(true),peer=viewer.peers[0];peer.stats=snapshot('first',1,1000,60);await viewer.intervals[0]();peer.stats=snapshot('first',1,2000,120);await viewer.intervals[0]();assert.match(viewer.nodes.stats.textContent,/接收 60 fps/);
    peer.stats=value;await viewer.intervals[0]();assert.match(viewer.nodes.stats.textContent,/接收 — fps/,'Changed stream or invalid interval must reset frame-rate measurement');assert.match(viewer.nodes.stats.textContent,/画面缓冲 — ms/);assert(!viewer.nodes.stats.textContent.includes('声音缓冲'),'No audio metric must be fabricated for a video-only report');
    viewer.advanceTime(5000);await viewer.intervals[0]();const report=viewer.sent.findLast(event=>event.receiverReport).receiverReport;for(const field of ['fps','bufferMs','decodeMs','mbps','videoDecodedDelta'])assert.equal(report[field],null,field+' must be unknown for a changed stream or invalid interval');
    const last=value.get('video');peer.stats=snapshot(last.id,last.ssrc,last.timestamp+1000,last.framesDecoded+60);await viewer.intervals[0]();assert.match(viewer.nodes.stats.textContent,/接收 60 fps/);
    peer.stats=new Map([['video',{id:last.id,ssrc:last.ssrc,type:'inbound-rtp',kind:'video',timestamp:last.timestamp+2000,bytesReceived:400000}]]);await viewer.intervals[0]();assert.match(viewer.nodes.stats.textContent,/接收 — fps/);assert.match(viewer.nodes.stats.textContent,/画面缓冲 — ms/);
  }
}
function mockAudioContext(options={}){
  const contexts=[];
  class MockContext{
    state='suspended';onstatechange=null;baseLatency=.005;outputLatency=.01;sources=[];gains=[];resumeCalls=0;destination={};
    constructor(config){this.config=config;contexts.push(this);if(options.constructFails)throw new Error('No audio device')}
    async resume(){this.resumeCalls++;if(options.resumeFails)throw new Error('Gesture required');if(options.resumePending)await options.resumePending;this.state='running';this.onstatechange?.()}
    createMediaStreamSource(stream){if(options.sourceFails)throw new Error('Stream source unavailable');const source={stream,disconnected:false,connect(){},disconnect(){this.disconnected=true}};this.sources.push(source);return source}
    createGain(){const gain={gain:{value:0},disconnected:false,connect(){},disconnect(){this.disconnected=true}};this.gains.push(gain);return gain}
  }
  return {MockContext,contexts};
}
function attachAudio(viewer){const peer=viewer.peers.at(-1),track={kind:'audio',readyState:'live',stop(){this.readyState='ended'}};peer.ontrack({track,receiver:{}});viewer.channels.at(-1).onmessage({data:JSON.stringify({type:'pong',audio:true,fps:60})});return track}
{
  const mock=mockAudioContext(),viewer=await start(true,scripts,undefined,mock.MockContext),audio=viewer.nodes.desktopAudio;
  attachAudio(viewer);assert.equal(mock.contexts.length,0,'Only a sound-button user gesture may create or resume the audio context');
  await viewer.nodes.audio.onclick();const context=mock.contexts[0];
  assert.equal(context.config.latencyHint,'interactive');assert.equal(context.state,'running');assert.equal(context.resumeCalls,1);
  assert.equal(viewer.navigator.audioSession.type,'playback');
  assert.equal(audio.muted,true,'The element must remain muted while Web Audio is audible');assert.equal(audio.playCalls,0,'Web Audio must not also start the element playback path');assert.equal(context.gains[0].gain.value,1);
  assert.equal(context.sources[0].stream,viewer.nodes.desktopAudio.srcObject);
  const stats=second=>new Map([['video',{type:'inbound-rtp',kind:'video',timestamp:second*1000,framesDecoded:second*60,bytesReceived:second*10000,jitterBufferDelay:second,jitterBufferEmittedCount:second*60}]]);
  viewer.peers[0].stats=stats(1);await viewer.intervals[0]();viewer.peers[0].stats=stats(2);await viewer.intervals[0]();await viewer.intervals[0]();
  const report=viewer.sent.findLast(event=>event.receiverReport).receiverReport;assert.equal(report.audioBaseLatencyMs,5);assert.equal(report.audioOutputLatencyMs,10);
  assert.equal(report.audioEnabled,1);assert.equal(report.audioContextState,1);assert.equal(report.audioOutputMode,1);assert.equal(report.audioElementMuted,1);
  await viewer.nodes.audio.onclick();assert.equal(context.sources[0].disconnected,true);assert.equal(audio.muted,true);assert.equal(viewer.nodes.audio.textContent,'开启声音');
  await viewer.nodes.audio.onclick();assert.equal(mock.contexts.length,1);assert.equal(context.resumeCalls,1,'Unmuting an already running context must reuse the unlocked context');
  const oldSource=context.sources.at(-1);viewer.peers[0].connectionState='failed';viewer.peers[0].onconnectionstatechange();await viewer.fireTimer(3000);
  assert.equal(oldSource.disconnected,true,'Reconnection must disconnect the old stream immediately');attachAudio(viewer);await flush();
  assert.equal(mock.contexts.length,1);assert.equal(context.resumeCalls,1,'Reconnection must not attempt resume without a user gesture');assert.equal(context.sources.at(-1).stream,audio.srcObject);assert.equal(audio.muted,true);
  await viewer.nodes.stop.onclick();assert.equal(context.sources.at(-1).disconnected,true);assert.equal(audio.srcObject,null);
}
{
  const mock=mockAudioContext(),viewer=await start(true,scripts,undefined,mock.MockContext);attachAudio(viewer);
  await viewer.nodes.audio.onclick();const context=mock.contexts[0];
  context.state='interrupted';context.onstatechange();assert.equal(viewer.nodes.audio.textContent,'恢复声音');
  await viewer.nodes.audio.onclick();assert.equal(context.state,'running');assert.equal(context.resumeCalls,2);assert.equal(viewer.nodes.audio.textContent,'静音');
  assert.equal(viewer.nodes.desktopAudio.muted,true,'Resuming Web Audio must keep only one audible path');
  viewer.nodes.audioMode.value='compatible';await viewer.nodes.audioMode.onchange();
  assert.equal(context.sources.at(-1).disconnected,true);assert.equal(viewer.nodes.desktopAudio.muted,false);assert.equal(viewer.nodes.desktopAudio.playCalls,1);
  context.state='suspended';context.onstatechange();assert.equal(viewer.nodes.audio.textContent,'静音','An unused suspended context must not affect the compatible player');
  await viewer.nodes.audio.onclick();assert.equal(viewer.nodes.desktopAudio.muted,true);assert.equal(viewer.nodes.audio.textContent,'开启声音');
  viewer.nodes.audioMode.value='low';await viewer.nodes.audioMode.onchange();assert.equal(context.resumeCalls,3);assert.equal(viewer.nodes.desktopAudio.muted,true);assert.equal(viewer.nodes.audio.textContent,'静音');
}
{
  const mock=mockAudioContext(),viewer=await start(true,scripts,undefined,mock.MockContext);attachAudio(viewer);
  viewer.nodes.audioMode.value='compatible';await viewer.nodes.audioMode.onchange();
  assert.equal(mock.contexts.length,0,'Explicit compatible playback must work without Web Audio');assert.equal(viewer.nodes.desktopAudio.muted,false);assert.equal(viewer.nodes.audio.textContent,'静音');
}
{
  const mock=mockAudioContext({sourceFails:true}),viewer=await start(true,scripts,undefined,mock.MockContext);attachAudio(viewer);
  await viewer.nodes.audio.onclick();assert.equal(viewer.nodes.desktopAudio.muted,false,'Unsupported Web Audio must fall back to element playback');assert.equal(viewer.nodes.desktopAudio.playCalls,1);assert.match(viewer.nodes.notice.textContent,/兼容播放/);
  await viewer.nodes.audio.onclick();assert.equal(viewer.nodes.desktopAudio.muted,true);assert.equal(viewer.nodes.audio.textContent,'开启声音');
}
{
  const options={resumeFails:true},mock=mockAudioContext(options),viewer=await start(true,scripts,undefined,mock.MockContext);attachAudio(viewer);viewer.nodes.desktopAudio.playFailure=true;
  await viewer.nodes.audio.onclick();assert.equal(viewer.nodes.desktopAudio.muted,true);assert.equal(viewer.nodes.audio.textContent,'开启声音');
  options.resumeFails=false;viewer.nodes.desktopAudio.playFailure=false;await viewer.nodes.audio.onclick();
  assert.equal(mock.contexts.length,1);assert.equal(mock.contexts[0].resumeCalls,2,'A later user gesture must retry a rejected audio unlock');assert.equal(viewer.nodes.desktopAudio.muted,true);assert.equal(mock.contexts[0].sources.length,1);
}
for(const action of ['mute','reconnect']){
  let resolveResume;const mock=mockAudioContext({resumePending:new Promise(resolve=>resolveResume=resolve)}),viewer=await start(true,scripts,undefined,mock.MockContext);attachAudio(viewer);
  const unlocking=viewer.nodes.audio.onclick();await flush();
  if(action==='mute')await viewer.nodes.audio.onclick();else await viewer.nodes.connect.onclick();
  resolveResume();await unlocking;await flush();
  assert.equal(mock.contexts[0].sources.length,0,'A late resume after '+action+' must not attach the old audio stream');assert.equal(viewer.nodes.desktopAudio.muted,true);assert.equal(viewer.nodes.desktopAudio.playCalls,0);
}
console.log('Verified reconnect/input regressions, precise receiver diagnostics, interactive Web Audio, compatible fallback, unlock retries, mute races and media cleanup. No network or media was used.');
