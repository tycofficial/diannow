import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const html=(await readFile('web/viewer.html','utf8')).replace('//__GESTURES__',(await readFile('web/gestures.mjs','utf8')).replaceAll('export class','class'));
assert.match(html,/<option value="480" selected>/,'The actual initial HTML selection must request 480P');
assert.doesNotMatch(html,/<option value="1080" selected>/);
const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match=>match[1]);
assert.equal(scripts.length,2);
async function flush(){for(let i=0;i<60;i++)await Promise.resolve()}
async function start(rtcAvailable,sources=scripts,fetchResponse,AudioContextClass,serverBuild=12,sessionStorage,initialHidden=false,iceConfiguration,viewerHost='screen.example'){
  const navigations=[];const nodes={},stages=[],requests=[],peers=[],timers=new Map(),channels=[],sent=[],intervals=[],animationFrames=[],documentListeners={};let timerId=0,clockNow=Date.now();
  for(const [,id] of html.matchAll(/id="([^"]+)"/g)){
    let open=false;
    const listeners={};
    nodes[id]={style:{},children:[],textContent:'',value:id==='quality'?'480':id==='deviceMode'?'desktop':id==='typingMode'?'live':id==='audioMode'?'low':id==='typing'?'':'60',options:[],listeners,muted:['screen','desktopAudio'].includes(id),playCalls:0,
      classList:{toggle(){open=!open;return open}},attributes:{},setAttribute(name,value){this.attributes[name]=String(value)},appendChild(child){this.children.push(child)},addEventListener(type,listener){(listeners[type]??=[]).push(listener)},focus(){this.focusCalls=(this.focusCalls||0)+1},blur(){},async play(){this.playCalls++;if(this.playFailure)throw new Error('Playback blocked')},setPointerCapture(){},getBoundingClientRect(){return {left:0,top:0,width:1280,height:720}}};
  }
  class Peer{
    iceGatheringState='complete';connectionState='new';
    remoteDescriptions=0;
    constructor(config){this.config=config;peers.push(this)}
    createDataChannel(label){const channel={label,readyState:'open',bufferedAmount:0,send(value){if(channel.fail)throw new Error('Data channel failed');sent.push(JSON.parse(value))}};channels.push(channel);return channel}
    addTransceiver(kind){const transceiver={kind,sender:{replaceCalls:[],async replaceTrack(track){this.replaceCalls.push(track);if(this.replaceHandler)return await this.replaceHandler(track);this.track=track}}};(this.transceivers??=[]).push(transceiver);return transceiver}
    async createOffer(){return {type:'offer',sdp:'test-sdp'}}
    async setLocalDescription(value){this.localDescription=value}
    async setRemoteDescription(){this.remoteDescriptions++;if(this.connectionState==='closed')throw new Error('Cannot answer a closed peer');this.connectionState='connected';this.onconnectionstatechange()}
    async getStats(){return this.stats||new Map()}
    close(){this.connectionState='closed';this.onconnectionstatechange?.()}
  }
  const windowListeners={};const window={addEventListener(type,listener){(windowListeners[type]??=[]).push(listener)},isSecureContext:true,AudioContext:AudioContextClass,sessionStorage};window.self=window;window.top=window;
  // WebKit rejects Window timer functions invoked as methods of TouchGestures.
  // Bare global calls and calls on Window remain valid.
  function wrongReceiver(value){return value&&Object.prototype.toString.call(value.pointers)==='[object Map]'}
  function setTimer(callback,delay){if(wrongReceiver(this))throw new TypeError('Can only call Window.setTimeout on instances of Window');timers.set(++timerId,{callback,delay});return timerId}
  function clearTimer(id){if(wrongReceiver(this))throw new TypeError('Can only call Window.clearTimeout on instances of Window');timers.delete(id)}
  const navigator={audioSession:{type:'auto'}};
  const document={hidden:initialHidden,body:{classList:{toggle(){}}},getElementById:id=>nodes[id],querySelectorAll:()=>[],createElement:()=>({children:[],appendChild(child){this.children.push(child)},classList:{add(){},remove(){}}}),addEventListener(type,listener){(documentListeners[type]??=[]).push(listener)}};
  const context=vm.createContext({window,navigator,document,
    location:{hostname:viewerHost,href:'https://'+viewerHost+'/',replace(value){navigations.push(value)}},setTimeout:setTimer,clearTimeout:clearTimer,clearInterval(){},setInterval(callback){intervals.push(callback)},requestAnimationFrame(callback){animationFrames.push(callback)},performance:{now:()=>clockNow},
    Date:class extends Date{static now(){return clockNow}},URL,AbortController,MediaStream:class{tracks=[];addTrack(track){this.tracks.push(track)}getTracks(){return this.tracks}getAudioTracks(){return this.tracks.filter(track=>track.kind==='audio')}},RTCPeerConnection:rtcAvailable?Peer:undefined,
    XMLHttpRequest:class{open(){}setRequestHeader(){}send(value){stages.push(JSON.parse(value).stage)}},
    fetch:async(path,options)=>{if(path.endsWith('/api/version'))return {ok:true,json:async()=>({build:serverBuild})};if(path.endsWith('/api/ice'))return {ok:true,json:async()=>iceConfiguration||({iceServers:[{urls:'turn:relay.example:3478?transport=tcp',username:'test',credential:'test'}],relayAvailable:true})};requests.push({path,options});return fetchResponse?await fetchResponse(path,options):{ok:true,status:200,json:async()=>({type:'answer',sdp:'answer-sdp'})}}});
  // Reproduce a host that already has common top-level names. Viewer must stay scoped.
  vm.runInContext('const video=1, keys=2, pc=3;',context);
  for(const script of sources)vm.runInContext(script,context);
  await flush();
  nodes.keyboard.onclick();
  assert.equal(nodes.keyboard.textContent,'收起键盘');
  if(stages.includes('connected')){
    channels[0].onmessage({data:JSON.stringify({type:'pong',inputFeatures:['nativeTouch','keyState','relativePointer']})});
    const event={pointerId:1,pointerType:'touch',clientX:50,clientY:50,preventDefault(){}};
    nodes.screen.onpointerdown(event);nodes.screen.onpointerup(event);
  }
  async function fireTimer(delay){const entry=[...timers].find(([,timer])=>timer.delay===delay);assert(entry,'Expected a '+delay+' ms timer');timers.delete(entry[0]);entry[1].callback();await flush()}
  function dispatch(id,type,properties={}){const event={prevented:false,preventDefault(){this.prevented=true},...properties};for(const listener of nodes[id].listeners[type]||[])listener(event);return event}
  return {nodes,stages,requests,peers,timers,navigations,setServerBuild(value){serverBuild=value},fireTimer,channels,sent,dispatch,intervals,navigator,document,dispatchDocument(type,event){for(const listener of documentListeners[type]||[])listener(event)},stepFrame(){for(const callback of animationFrames.splice(0))callback()},dispatchWindow(type,event){for(const listener of windowListeners[type]||[])listener(event)},advanceTime:ms=>clockNow+=ms};
}
const connected=await start(true);
assert(connected.stages.includes('connected'));
assert.equal(connected.peers[0].config.iceServers[0].username,'test');
assert.equal(connected.peers[0].config.iceTransportPolicy,'all');
assert.equal(connected.peers[0].config.bundlePolicy,'balanced');
assert.equal(JSON.parse(connected.requests[0].options.body).separateControl,true);
assert.equal(JSON.parse(connected.requests[0].options.body).preferences.quality,'480');
assert.equal(JSON.parse(connected.requests[0].options.body).preferences.fps,60);
assert.equal(JSON.parse(connected.requests[0].options.body).preferences.fixedProfile,true);
assert.equal(connected.requests[0].options.credentials,'omit');
const unavailable=await start(false);
assert(unavailable.stages.includes('connection-error'));
assert.match(unavailable.nodes.overlay.textContent,/没有提供/);
assert(connected.sent.some(e=>e.type==='touch'&&e.phase==='down'),'Touch responds immediately without a hold timer');
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
  await viewer.fireTimer(30000);await viewer.fireTimer(1000);
  assert.match(viewer.nodes.status.textContent,/已连接/);
  assert.equal(viewer.peers.length,2);
  const state={status:viewer.nodes.status.textContent,overlay:viewer.nodes.overlay.textContent,stages:viewer.stages.length,timers:new Set(viewer.timers.keys())};
  viewer.finish(response(status,{type:'answer',sdp:'stale-answer'}));await flush();
  assert.equal(viewer.nodes.status.textContent,state.status,'A stale '+status+' answer must preserve the new connection status');
  assert.equal(viewer.nodes.overlay.textContent,state.overlay,'A stale answer must preserve the new connection overlay');
  assert.equal(viewer.stages.length,state.stages,'A stale answer must not emit connection stages');
  assert.equal(viewer.peers[0].remoteDescriptions,0,'A stale success must not answer the closed peer');
  assert(![...viewer.timers].some(([id,timer])=>!state.timers.has(id)&&[1000,5000].includes(timer.delay)),'A stale answer must not schedule another retry');
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
  assert(![...viewer.timers].some(([id,timer])=>!state.timers.has(id)&&[1000,5000].includes(timer.delay)),'A late answer must not reconnect after intentional disconnect');
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
  assert.equal(videoReceiver.jitterBufferTarget,0);assert.equal(videoReceiver.playoutDelayHint,0);assert.equal(audioReceiver.jitterBufferTarget,0);assert.equal(audioReceiver.playoutDelayHint,0)
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
  assert.match(nodes.stats.textContent,/解码 60 fps · 发送 60 fps/,'Receiver and sender frame rates must remain distinguishable');
  assert.equal(peers.length,1,'A high jitter buffer must not trigger reconnection');
  peer.stats=stats(3000,180,undefined,undefined);await viewer.intervals[0]();assert.match(nodes.stats.textContent,/缓冲 — ms/,'Unsupported delay statistics must not appear as zero');
  const reports=()=>viewer.sent.filter(event=>event.receiverReport);
  assert.equal(reports().length,1,'The prior valid measurement must ride the next ping');
  assert.deepEqual(reports()[0].receiverReport,{receivedFps:null,presentedFps:null,videoReceivedDelta:null,videoPresentedDelta:null,videoPresentationSource:null,videoPresentationAgeMs:null,viewerVisible:1,videoRequestedEnabled:1,videoPaused:null,audioRms:null,audioEnabled:0,audioContextState:null,audioOutputMode:0,audioElementMuted:1,fps:60,bufferMs:300,lossDelta:1,decodeMs:4,mbps:.05,inputAckMs:null,inputAckAgeMs:null,audioBufferMs:null,audioTotalSamplesDuration:null,audioTotalSamplesReceived:null,audioConcealedSamplesDelta:null,audioPlayoutSamplesDuration:null,audioTotalPlayoutDelay:null,audioTotalSamplesCount:null,audioPlayoutMs:null,audioBaseLatencyMs:null,audioOutputLatencyMs:null,videoDecodedDelta:60,videoKeyFramesDelta:null,videoPliDelta:null,videoNackDelta:null,videoFreezeDelta:null,videoDroppedDelta:null,playbackFramesDelta:null,playbackDroppedDelta:null});
  await viewer.intervals[0]();assert.equal(reports().length,1,'Receiver reports must be throttled rather than sent every second');
  viewer.advanceTime(5000);await viewer.intervals[0]();assert.equal(reports().length,2);assert.equal(reports()[1].receiverReport.bufferMs,null,'Unsupported buffer statistics must be reported as unknown');
  const videoStream=nodes.screen.srcObject,audioStream=nodes.desktopAudio.srcObject;
  peer.connectionState='failed';peer.onconnectionstatechange();await viewer.fireTimer(1000);
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
  peer.stats=richStats(1);await viewer.intervals[0]();viewer.advanceTime(1000);playback={totalVideoFrames:120,droppedVideoFrames:7};peer.stats=richStats(2);await viewer.intervals[0]();assert.match(viewer.nodes.stats.textContent,/画面缓冲 29 ms · 声音关闭/);viewer.advanceTime(1000);await viewer.intervals[0]();
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
    const viewer=await start(true),peer=viewer.peers[0];peer.stats=snapshot('first',1,1000,60);await viewer.intervals[0]();peer.stats=snapshot('first',1,2000,120);await viewer.intervals[0]();assert.match(viewer.nodes.stats.textContent,/解码 60 fps/);
    peer.stats=value;await viewer.intervals[0]();assert.match(viewer.nodes.stats.textContent,/解码 — fps/,'Changed stream or invalid interval must reset frame-rate measurement');assert.match(viewer.nodes.stats.textContent,/画面缓冲 — ms/);assert(!viewer.nodes.stats.textContent.includes('声音缓冲'),'No audio metric must be fabricated for a video-only report');
    viewer.advanceTime(5000);await viewer.intervals[0]();const report=viewer.sent.findLast(event=>event.receiverReport).receiverReport;for(const field of ['fps','bufferMs','decodeMs','mbps','videoDecodedDelta'])assert.equal(report[field],null,field+' must be unknown for a changed stream or invalid interval');
    const last=value.get('video');peer.stats=snapshot(last.id,last.ssrc,last.timestamp+1000,last.framesDecoded+60);await viewer.intervals[0]();assert.match(viewer.nodes.stats.textContent,/解码 60 fps/);
    peer.stats=new Map([['video',{id:last.id,ssrc:last.ssrc,type:'inbound-rtp',kind:'video',timestamp:last.timestamp+2000,bytesReceived:400000}]]);await viewer.intervals[0]();assert.match(viewer.nodes.stats.textContent,/解码 — fps/);assert.match(viewer.nodes.stats.textContent,/画面缓冲 — ms/);
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
  const oldSource=context.sources.at(-1);viewer.peers[0].connectionState='failed';viewer.peers[0].onconnectionstatechange();await viewer.fireTimer(1000);
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

const rawStart=connected.sent.length;
for(const [type,down] of [['keydown',true],['keyup',false]])connected.dispatchWindow(type,{key:'a',code:'KeyA',preventDefault(){},target:{tagName:'BODY'}});
assert.deepEqual(connected.sent.slice(rawStart).map(e=>[e.type,e.code,e.down]),[['keyState','KeyA',true],['keyState','KeyA',false]]);
const ignored=connected.sent.length;connected.dispatchWindow('keydown',{key:'a',code:'KeyA',preventDefault(){},target:{tagName:'TEXTAREA'}});assert.equal(connected.sent.length,ignored);
console.log('Hardware keyboard routes without opening the on-screen keyboard and preserves local text editing.');

const virtualButtons=connected.nodes.oskRows.children.flatMap(row=>row.children);
assert(virtualButtons.length>=96);
for(const label of ['A','1 !','; :','/ ?',"' \"",'Caps','F12']){
 const button=virtualButtons.find(b=>b.textContent===label);assert(button,'Full keyboard includes '+label);const start=connected.sent.length;
 button.onpointerdown({pointerId:99,preventDefault(){}});button.onpointerup();
 const keys=connected.sent.slice(start).filter(e=>e.type==='keyState');assert.equal(keys.length,2);assert.equal(keys[0].down,true);assert.equal(keys[1].down,false);
}
console.log('Full Windows keyboard includes letters, numbers, punctuation, Caps and function keys with raw down/up events.');

{
 const viewer=await start(true),peer=viewer.peers[0],control=viewer.channels.find(c=>c.label==='input');
 viewer.advanceTime(16000);
 control.onmessage({data:JSON.stringify({type:'pong',time:0})});
 await viewer.intervals[0]();
 assert.equal(viewer.peers.length,1,'A stalled decoder with a live control channel must not tear down the peer');
 assert(viewer.sent.some(e=>e.type==='video-recover'),'Recover with an IDR before any full reconnect');
 assert(viewer.nodes.screen.playCalls>0,'Retry the existing player');
 await viewer.fireTimer(12000);assert(![...viewer.timers.values()].some(timer=>timer.delay===30000),'Clear the initial deadline once connected');
 assert.equal(viewer.peers.length,1,'Initial connection deadlines cannot disconnect a peer that has already connected');
 control.readyState='connecting';
 const before=viewer.sent.length;
 viewer.nodes.screen.onpointerdown({pointerId:5,pointerType:'touch',preventDefault(){},clientX:40,clientY:40});
 assert.equal(viewer.sent.length,before,'Do not silently accept a touch before the data channel opens');
 assert.match(viewer.nodes.notice.textContent,/操作通道/);
 control.readyState='open';control.onopen();assert.match(viewer.nodes.status.textContent,/操作可用/);
 viewer.channels[0].onmessage({data:JSON.stringify({type:'cursor',cursor:{x:.2,y:.3,visible:true}})});
 assert(parseFloat(viewer.nodes.remoteCursor.style.width)<=10,'Remote cursor must stay compact');
 console.log('Verified video-stall recovery, stale deadline immunity, data-channel readiness and compact cursor.');
}

{
 const retry=await start(true);retry.peers[0].connectionState='failed';retry.peers[0].onconnectionstatechange();await retry.fireTimer(1000);assert.equal(retry.peers[1].config.iceTransportPolicy,'relay');assert.deepEqual(JSON.parse(JSON.stringify(retry.peers[1].config.iceServers[0].urls)),['turn:relay.example:3478?transport=tcp']);console.log('Verified failed paths retry on a TCP relay and negotiate separate control.');
}

{
 const updates=await start(true);updates.setServerBuild(21);updates.nodes.typing.value='unsent draft';await updates.intervals[1]();assert.equal(updates.navigations.length,0);updates.nodes.typing.value='';await updates.intervals[1]();assert.equal(updates.navigations.length,1);assert.match(updates.navigations[0],/_release=21/);await updates.intervals[1]();assert.equal(updates.navigations.length,1);console.log('Verified automatic release reload, cache bypass and unsent draft protection.');
}

const pointer=(overrides={})=>({pointerId:77,pointerType:'touch',clientX:100,clientY:100,button:0,preventDefault(){},...overrides});
{
 const viewer=await start(true),control=viewer.channels[0],screen=viewer.nodes.screen;
 const before=viewer.sent.length;
 control.bufferedAmount=65536;
 screen.onpointerdown(pointer());screen.onpointerup(pointer());
 assert.equal(viewer.sent.length,before,'Congested critical boundaries wait rather than disappear');
 control.bufferedAmount=0;control.onbufferedamountlow();
 assert.deepEqual(viewer.sent.slice(before).map(e=>e.phase),['down','up'],'Native touch down/up drain in order');
 assert(viewer.sent.slice(before).every(e=>e.gesture>0&&e.touchSeq>0));
 control.bufferedAmount=65536;screen.onpointerdown(pointer({pointerId:78}));screen.onpointerup(pointer({pointerId:78}));
 viewer.advanceTime(2100);control.bufferedAmount=0;control.onbufferedamountlow();
 assert.equal(viewer.sent.at(-1).type,'release','Expired actions release instead of replaying old taps');
 assert(!viewer.nodes.hardwareInput.focusCalls,'Touch gestures must not summon the hidden software keyboard');
}
{
 const viewer=await start(true),screen=viewer.nodes.screen;
 screen.setPointerCapture=()=>{throw new Error('Pointer is no longer active')};
 const before=viewer.sent.length;screen.onpointerdown(pointer());
 viewer.dispatchWindow('pointerup',pointer({target:{tagName:'BODY'}}));
 assert.deepEqual(viewer.sent.slice(before).filter(e=>e.type==='touch').map(e=>e.phase),['down','up'],'A capture failure still receives release outside the player');
 screen.onpointerdown(pointer());screen.onlostpointercapture(pointer());
 assert.equal(viewer.sent.at(-1).type,'release');
 viewer.stepFrame();assert.equal(viewer.sent.at(-1).type,'release','Capture loss discards pending movement');
}
{
 const viewer=await start(true),screen=viewer.nodes.screen,mouse=pointer({pointerType:'mouse'});
 screen.onpointerdown(mouse);screen.onpointermove({...mouse,clientX:200});screen.onpointerup({...mouse,clientX:300});
 const up=viewer.sent.at(-1);assert.equal(up.type,'button');assert.equal(up.down,false);
 assert(up.seq>viewer.sent.at(-2).seq,'Mouse release advances the movement ordering barrier');
 const before=viewer.sent.length;viewer.stepFrame();assert.equal(viewer.sent.length,before,'An unsent mouse movement cannot jump back after mouseup');
}
{
 const viewer=await start(true),buttons=viewer.nodes.oskRows.children.flatMap(row=>row.children),button=buttons.find(b=>b.textContent==='A');
 button.onpointerdown(pointer());assert([...viewer.timers.values()].some(t=>t.delay===400));
 viewer.dispatchWindow('blur',{});
 assert(![...viewer.timers.values()].some(t=>t.delay===400),'Blur cancels the pending repeat timer');
 const before=viewer.sent.length;for(const tick of viewer.intervals.slice(2))tick();
 assert.equal(viewer.sent.length,before,'Released keyboard keys cannot start repeating again');
}
console.log('Verified congested touch boundaries, stale-input expiry, lost pointer capture, final mouse position and keyboard repeat cleanup.');

{
 const viewer=await start(true),pad=viewer.nodes.trackpad,mode=viewer.nodes.deviceMode,control=viewer.channels[0];
 mode.value='trackpad';mode.onchange();assert.equal(pad.hidden,false);assert.equal(viewer.nodes.remoteCursor.hidden,true,'Trackpad mode hides the remote desktop cursor immediately');
 control.onmessage({data:JSON.stringify({type:'pong',time:0,inputFeatures:['relativePointer'],cursor:{x:.2,y:.3,visible:true}})});
 assert.equal(viewer.nodes.remoteCursor.hidden,true,'Server cursor reports cannot place a cursor over the trackpad');
 pad.setPointerCapture=()=>{throw new Error('Pointer capture unavailable')};
 const before=viewer.sent.length;pad.onpointerdown(pointer({clientX:10,clientY:20}));
 viewer.dispatchWindow('pointermove',pointer({target:{tagName:'BODY'},clientX:40,clientY:50}));
 viewer.stepFrame();
 assert(viewer.sent.slice(before).some(e=>e.type==='relative'&&e.totalX>0),'Movement outside the pad still works after pointer capture fails');
 viewer.dispatchWindow('pointerup',pointer({target:{tagName:'BODY'},clientX:45,clientY:55}));
 const checkpoint=viewer.sent.at(-1);assert.equal(checkpoint.type,'relative');assert.equal(checkpoint.checkpoint,true);
 assert.equal(checkpoint.totalX,52.5);assert.equal(checkpoint.totalY,52.5,'The reliable final checkpoint contains the complete gesture, including pointerup movement');
 const count=viewer.sent.length;viewer.stepFrame();assert.equal(viewer.sent.length,count,'A completed reliable checkpoint must not replay its pending movement');
 viewer.advanceTime(151);viewer.stepFrame();assert.equal(viewer.sent.length,count,'Idle pads do not send repeated reliable snapshots');
 pad.onpointerdown(pointer({clientX:10,clientY:20}));pad.onpointermove(pointer({clientX:20,clientY:30}));viewer.advanceTime(151);viewer.stepFrame();
 assert.equal(viewer.sent.at(-1).checkpoint,true,'Active movement also receives a bounded reliable checkpoint');
 mode.value='desktop';mode.onchange();assert.equal(pad.hidden,true);assert.equal(viewer.nodes.remoteCursor.hidden,false);
 const legacy=await start(true),legacyPad=legacy.nodes.trackpad;
 legacy.channels[0].onmessage({data:JSON.stringify({type:'pong',time:0,inputFeatures:[]})});
 legacyPad.onpointerdown(pointer({clientX:0,clientY:0}));legacyPad.onpointermove(pointer({clientX:20,clientY:0}));legacy.stepFrame();
 const moves=legacy.sent.filter(e=>e.type==='move').length;legacyPad.onpointerup(pointer({clientX:20,clientY:0}));legacy.advanceTime(151);legacy.stepFrame();
 assert.equal(legacy.sent.filter(e=>e.type==='move').length,moves,'Legacy absolute fallback must not replay deltas in reliable checkpoints');
 console.log('Verified trackpad cursor isolation, capture failure motion, reliable final/periodic positions and compatible legacy movement. No desktop input was injected.');
}

{
 const viewer=await start(true),event={key:'Shift',code:'ShiftLeft',preventDefault(){},target:{tagName:'BODY'}};
 viewer.dispatchWindow('keydown',event);
 viewer.dispatchWindow('keyup',{...event,isComposing:true,target:{tagName:'SELECT'}});
 assert.equal(viewer.sent.at(-1).code,'ShiftLeft');assert.equal(viewer.sent.at(-1).down,false,'A key held before focus changes must still be released in a local control');
 const before=viewer.sent.length;
 viewer.dispatchWindow('keydown',{...event,code:'KeyW',target:{tagName:'TEXTAREA'}});
 viewer.dispatchWindow('keyup',{...event,code:'KeyW',target:{tagName:'TEXTAREA'}});
 assert.equal(viewer.sent.length,before,'Typing in local fields remains local');
 console.log('Verified held hardware keys release across focus and composition changes.');
}
{
 const viewer=await start(true);viewer.channels[0].bufferedAmount=65536;
 viewer.nodes.typing.value='保留未发送的文字';viewer.dispatch('typing','input');
 assert.equal(viewer.nodes.typing.value,'保留未发送的文字');assert.equal(viewer.nodes.typingMode.value,'draft');
 viewer.channels[0].bufferedAmount=0;viewer.nodes.sendTyping.onclick();
 assert.equal(viewer.sent.at(-1).text,'保留未发送的文字');
 console.log('Verified failed live text is retained as a sendable draft.');

{
 const viewer=await start(true),peer=viewer.peers[0],control=viewer.channels[0];
 for(const gap of [4000,6000,3001]){viewer.advanceTime(gap);control.onmessage({data:JSON.stringify({type:'pong',time:0})});await viewer.intervals[0]();await flush()}
 assert(viewer.sent.filter(e=>e.type==='video-recover').length>=2,'Try key-frame recovery before replacing a stalled video session');
 assert.equal(viewer.peers.length,2,'A dead video sender must reconnect after bounded recovery even while control pongs remain healthy');
 assert(viewer.stages.includes('video-stall-reconnect'));
}
{
 const viewer=await start(true),peer=viewer.peers[0],screen=viewer.nodes.screen,callbacks=new Map();let callbackId=0;
 screen.requestVideoFrameCallback=callback=>{callbacks.set(++callbackId,callback);return callbackId};screen.cancelVideoFrameCallback=id=>callbacks.delete(id);
 peer.ontrack({track:{kind:'video',stop(){}},receiver:{}});
 const present=count=>{const [id,callback]=callbacks.entries().next().value;callbacks.delete(id);callback(0,{presentedFrames:count})};
 const snapshot=second=>new Map([['video',{id:'video',type:'inbound-rtp',kind:'video',timestamp:second*1000,framesReceived:second*60,framesDecoded:second*60,bytesReceived:second*100000,jitterBufferEmittedCount:second*60,jitterBufferDelay:second*.3}]]);
 for(let second=1;second<=3;second++){viewer.advanceTime(1000);present(second*20);peer.stats=snapshot(second);await viewer.intervals[0]()}
 assert.match(screen.srcObject.getTracks()[0].kind,/video/);
 assert.match(viewer.nodes.stats.textContent,/解码 60 fps/);assert.match(viewer.nodes.stats.textContent,/显示 20 fps/,'Decode throughput must not be labeled as smooth presentation');
 const firstRecovery=viewer.sent.filter(e=>e.type==='video-recover').length,plays=screen.playCalls;
 for(let second=4;second<=8;second++){viewer.advanceTime(1000);peer.stats=snapshot(second);await viewer.intervals[0]()}
 assert.equal(viewer.sent.filter(e=>e.type==='video-recover').length,firstRecovery,'A stalled compositor with healthy decoding must not request encoder keyframes');
 assert(screen.playCalls>plays,'Resume the existing video player when composition stalls');assert.equal(viewer.peers.length,1);
 const report=viewer.sent.findLast(e=>e.receiverReport)?.receiverReport;
 assert.equal(report.receivedFps,60);assert.equal(report.fps,60);assert.equal(report.presentedFps,0);assert.equal(report.videoPresentationSource,1);
 viewer.document.hidden=true;viewer.dispatchDocument('visibilitychange',{});const hiddenPlays=screen.playCalls;
 viewer.advanceTime(20000);peer.stats=snapshot(28);await viewer.intervals[0]();assert.equal(screen.playCalls,hiddenPlays,'A hidden tab must not trigger video recovery');assert.equal(viewer.peers.length,1);
 viewer.document.hidden=false;viewer.dispatchDocument('visibilitychange',{});assert.equal(screen.playCalls,hiddenPlays+1,'Returning to the visible page resumes its existing player');
 assert(viewer.stages.includes('video-resume'));present(600);peer.stats=snapshot(29);await viewer.intervals[0]();assert.equal(viewer.peers.length,1,'Hidden time must not count toward a stalled-stream reconnect');
 await viewer.nodes.stop.onclick();assert.equal(callbacks.size,0,'Disconnect cancels its frame observer');
}
{
 const viewer=await start(true),peer=viewer.peers[0];let calls=0,resolve;
 peer.getStats=()=>{calls++;return new Promise(done=>resolve=done)};
 const pending=viewer.intervals[0]();await viewer.intervals[0]();assert.equal(calls,1,'Slow statistics calls must not overlap or apply samples out of order');resolve(new Map());await pending;
}
console.log('Verified bounded dead-video recovery with live controls, measured compositor FPS, playback-only recovery, visible resume and serialized statistics sampling.');

{
 const viewer=await start(true),peer=viewer.peers[0],control=viewer.channels[0];
 const sample=second=>new Map([['video',{type:'inbound-rtp',kind:'video',timestamp:second*1000,framesReceived:second*60,framesDecoded:0,bytesReceived:second*100000}]]);
 peer.stats=sample(1);await viewer.intervals[0]();let second=1;
 for(const gap of [4000,6000,3001]){second+=gap/1000;viewer.advanceTime(gap);peer.stats=sample(second);control.onmessage({data:JSON.stringify({type:'pong',time:0})});await viewer.intervals[0]();await flush()}
 assert.equal(viewer.peers.length,2,'Complete received frames and healthy pongs cannot preserve a decoder that remains dead after repeated keyframe requests');
}
for(const stuck of ['pending','rejected']){
 const viewer=await start(true),peer=viewer.peers[0],control=viewer.channels[0];let calls=0,resolve;
 peer.getStats=()=>{calls++;return stuck==='pending'?new Promise(done=>resolve=done):Promise.reject(new Error('Statistics unavailable'))};
 const pending=viewer.intervals[0]();if(stuck==='rejected')await pending;
 const before=viewer.sent.filter(e=>e.type==='ping').length;
 for(const gap of [4000,6000,3001]){viewer.advanceTime(gap);control.onmessage({data:JSON.stringify({type:'pong',time:0})});await viewer.intervals[0]();await flush()}
 assert(viewer.sent.filter(e=>e.type==='ping').length>before,'Unavailable statistics cannot stop control heartbeats');
 assert.equal(viewer.peers.length,2,'Unavailable statistics cannot disable the video watchdog');
 if(stuck==='pending'){assert.equal(calls,1);resolve(new Map());await pending}
}
{
 const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value))}},tab=storage();
 const offerId=viewer=>JSON.parse(viewer.requests.find(e=>e.path==='/api/offer').options.body).viewerId;
 const first=await start(true,scripts,undefined,undefined,12,tab),reload=await start(true,scripts,undefined,undefined,12,tab),other=await start(true,scripts,undefined,undefined,12,storage());
 assert.equal(offerId(first),offerId(reload),'Reloading the same tab must replace its earlier server session');assert.notEqual(offerId(first),offerId(other),'A fresh tab storage receives a distinct viewer identity');
 const blocked=await start(true,scripts,undefined,undefined,12,{getItem(){throw new Error('Storage denied')},setItem(){throw new Error('Storage denied')}});assert(offerId(blocked),'Unavailable session storage still permits connection');
 const before=first.sent.filter(e=>e.type==='disconnect').length;
 first.document.hidden=true;first.dispatchDocument('visibilitychange',{});assert.equal(first.sent.filter(e=>e.type==='disconnect').length,before,'A temporary hidden page keeps its session');
 first.channels[0].bufferedAmount=65536;first.dispatchWindow('pagehide',{});assert.equal(first.sent.at(-1).type,'disconnect','Leaving the page sends a final disconnect even while ordinary input is queued');
}
console.log('Verified dead-decoder recovery despite incoming frames, unavailable-statistics recovery, per-tab session identity and final page disconnect.');
}

{
 const viewer=await start(true);
 viewer.channels[0].onmessage({data:JSON.stringify({type:'pong',time:0,source:{height:1080,refresh:120},display:{virtual:true}})});
 assert.equal(viewer.nodes.framerate.value,'60','A 120Hz source must not override the fixed 60fps selection');
 viewer.nodes.quality.value='720';viewer.nodes.quality.onchange();assert.equal(viewer.sent.at(-1).fixedProfile,true);
 viewer.nodes.quality.value='auto';viewer.nodes.quality.onchange();assert.equal(viewer.sent.at(-1).fixedProfile,false);
 viewer.nodes.quality.value='1080';viewer.nodes.quality.onchange();assert.equal(viewer.sent.at(-1).quality,'1080');assert.equal(viewer.sent.at(-1).fps,60);assert.equal(viewer.sent.at(-1).fixedProfile,true);
 console.log('Verified fixed 480P60 default, manual fixed 1080P60, explicit automatic mode and no source-refresh override.');
}

{
 const viewer=await start(true),peer=viewer.peers[0];
 const stats=second=>new Map([
 ['video',{id:'video',type:'inbound-rtp',kind:'video',transportId:'media',timestamp:second*1000,framesDecoded:second*60,bytesReceived:second*100000}],
 ['media',{type:'transport',selectedCandidatePairId:'video-pair'}],
 ['video-pair',{type:'candidate-pair',state:'succeeded',nominated:true,currentRoundTripTime:.012}],
 ['control-pair',{type:'candidate-pair',state:'succeeded',nominated:true,currentRoundTripTime:.600}]
 ]);
 peer.stats=stats(1);await viewer.intervals[0]();viewer.advanceTime(1000);peer.stats=stats(2);await viewer.intervals[0]();
 assert.match(viewer.nodes.stats.textContent,/视频往返 12 ms/,'Video RTT must follow its own ICE transport, not the last reported control pair');
 const missing=stats(3);delete missing.get('video').transportId;peer.stats=missing;viewer.advanceTime(1000);await viewer.intervals[0]();
 assert.match(viewer.nodes.stats.textContent,/视频往返 — ms/,'Ambiguous multi-transport RTT must remain unknown');
 console.log('Verified video-specific transport RTT and safe unknown fallback.');

{
 const mock=mockAudioContext(),viewer=await start(true,scripts,undefined,mock.MockContext),peer=viewer.peers[0],control=viewer.channels[0],screen=viewer.nodes.screen;
 attachAudio(viewer);await viewer.nodes.audio.onclick();const context=mock.contexts[0],audioStream=viewer.nodes.desktopAudio.srcObject;
 const videoTrack={kind:'video',stopped:false,stop(){this.stopped=true}};peer.ontrack({track:videoTrack,receiver:{}});
 let pauses=0;screen.pause=()=>pauses++;
 const audioEvents=viewer.sent.filter(e=>['audioState','microphoneState'].includes(e.type)).length,disconnects=viewer.sent.filter(e=>e.type==='disconnect').length,settings=viewer.sent.filter(e=>e.type==='settings').length;
 control.bufferedAmount=65536;viewer.document.hidden=true;viewer.dispatchDocument('visibilitychange',{});
 assert.equal(pauses,1);assert.match(viewer.nodes.stats.textContent,/后台画面已暂停/);assert.equal(videoTrack.stopped,false,'Hiding preserves the incoming video track for resume');
 control.bufferedAmount=0;control.onbufferedamountlow();assert.deepEqual(viewer.sent.filter(e=>e.type==='videoState').at(-1),{type:'videoState',enabled:false},'A congested hide transition reliably pauses its own sender');
 assert.equal(viewer.sent.filter(e=>['audioState','microphoneState'].includes(e.type)).length,audioEvents,'Pausing video must not change desktop sound or microphone state');
 assert.equal(viewer.nodes.desktopAudio.srcObject,audioStream);assert.equal(context.sources.at(-1).disconnected,false);assert.equal(viewer.sent.filter(e=>e.type==='disconnect').length,disconnects);assert.equal(viewer.peers.length,1);
 viewer.advanceTime(20000);await viewer.intervals[0]();assert(!viewer.sent.some(e=>e.type==='video-recover'),'Hidden zero FPS is intentional and must not request recovery');
 const plays=screen.playCalls;viewer.document.hidden=false;viewer.dispatchDocument('visibilitychange',{});await flush();
 assert.deepEqual(viewer.sent.filter(e=>e.type==='videoState').at(-1),{type:'videoState',enabled:true});assert.equal(screen.playCalls,plays+1);assert.equal(screen.muted,true);assert.equal(screen.playsInline,true);
 assert.equal(viewer.sent.filter(e=>e.type==='settings').length,settings,'Visibility transitions preserve fixed quality, frame-rate and bitrate settings');
 assert.equal(viewer.peers[0],peer);assert.equal(viewer.nodes.desktopAudio.srcObject,audioStream);
 viewer.document.hidden=true;control.onopen();assert.equal(viewer.sent.filter(e=>e.type==='videoState').at(-1).enabled,false,'A newly open control channel immediately reconciles hidden video state');
 viewer.channels[0].onmessage({data:JSON.stringify({type:'pong',videoEnabled:true})});assert.equal(viewer.sent.filter(e=>e.type==='videoState').at(-1).enabled,false,'A stale server state is corrected without changing the stream profile');
}
{
 const viewer=await start(true,scripts,undefined,undefined,12,undefined,true),peer=viewer.peers[0];
 const offer=JSON.parse(viewer.requests.find(e=>e.path==='/api/offer').options.body);assert.equal(offer.preferences.videoEnabled,false);assert.equal(offer.preferences.quality,'480');assert.equal(offer.preferences.fps,60);assert.equal(offer.preferences.fixedProfile,true);
 peer.ontrack({track:{kind:'video',stop(){}},receiver:{}});assert.equal(viewer.nodes.screen.playCalls,0,'A page connected in the background must not start its video element');assert.equal(viewer.sent.filter(e=>e.type==='videoState').at(-1).enabled,false);
}
{
 const viewer=await start(true),screen=viewer.nodes.screen;screen.playFailure=true;
 viewer.document.hidden=true;viewer.dispatchDocument('visibilitychange',{});viewer.document.hidden=false;viewer.dispatchDocument('visibilitychange',{});await flush();assert.match(viewer.nodes.overlay.textContent,/轻点画面恢复播放/);
 for(const gap of [4000,6000,6000]){viewer.advanceTime(gap);await viewer.intervals[0]();await flush()}
 assert(!viewer.sent.some(e=>e.type==='video-recover'),'A known Safari playback rejection must not trigger repeated encoder keyframes');assert.equal(viewer.peers.length,1,'Playback permission cannot be fixed by reconnecting the peer');
 screen.playFailure=false;const plays=screen.playCalls;viewer.dispatchWindow('pointerdown',{});await flush();assert.equal(screen.playCalls,plays+1,'The next user touch retries rejected video playback');
 viewer.dispatchWindow('pointerdown',{});assert.equal(screen.playCalls,plays+1,'Successful playback does not keep replaying on every touch');
}
console.log('Verified reliable per-session background video suspension, preserved audio and fixed profile, hidden startup and Safari gesture resume without keyframe storms.');

{
 const config={relayAvailable:true,iceServers:[
  {urls:'turn:192.0.2.7:3478?transport=tcp',username:'original-user',credential:'original-password',credentialType:'password'},
  {urls:['turn:[2001:db8::7]:3478?transport=tcp','turn:plain.example','turns:secure.example:5349?transport=tcp','turns:default-tls.example'],username:'ipv6-user',credential:'ipv6-password'},
  {urls:['turn:existing.example:3478?transport=tcp','turn:existing.example'],username:'plain-user',credential:'plain-password'},
  {urls:'TURN:upper.example:3478?transport=TCP',username:'case-user',credential:'case-password'},
  {urls:'stun:stun.example:3478'}
 ]},before=JSON.stringify(config),viewer=await start(true,scripts,undefined,undefined,12,undefined,false,config);
 const normalized=JSON.parse(JSON.stringify(viewer.peers[0].config.iceServers));
 assert.deepEqual(normalized[0].urls,['turn:192.0.2.7:3478?transport=udp','turn:192.0.2.7:3478?transport=tcp'],'The first configured TURN endpoint gathers UDP before its original TCP fallback');
 assert.deepEqual(normalized[1].urls,['turn:[2001:db8::7]:3478?transport=udp','turn:[2001:db8::7]:3478?transport=tcp','turn:plain.example','turns:secure.example:5349?transport=tcp','turns:default-tls.example'],'IPv6 brackets, plain TURN and secure TURN must retain their hosts, ports and schemes');
 assert.deepEqual(normalized[2].urls,['turn:existing.example','turn:existing.example:3478?transport=tcp'],'Reuse an existing plain UDP endpoint instead of duplicating its allocation');
 assert.deepEqual(normalized[3].urls,['TURN:upper.example:3478?transport=udp','TURN:upper.example:3478?transport=TCP']);assert.deepEqual(normalized[4].urls,['stun:stun.example:3478']);
 for(let index=0;index<config.iceServers.length;index++)for(const key of ['username','credential','credentialType'])assert.equal(normalized[index][key],config.iceServers[index][key],'TURN credentials must pass through unchanged');
 assert.equal(JSON.stringify(config),before,'Normalization must not mutate the API response');
 viewer.peers[0].connectionState='failed';viewer.peers[0].onconnectionstatechange();await viewer.fireTimer(1000);
 const fallbacks=JSON.parse(JSON.stringify(viewer.peers[1].config.iceServers));assert.deepEqual(fallbacks[1].urls,['turn:[2001:db8::7]:3478?transport=tcp','turns:secure.example:5349?transport=tcp','turns:default-tls.example'],'Relay retry retains every configured TCP and TLS fallback');
 const preferences=JSON.parse(viewer.requests.filter(request=>request.path==='/api/offer').at(-1).options.body).preferences;
 assert.equal(preferences.viewerRelayTransport,'tcp');assert.equal(preferences.relayTransport,undefined,'A browser fallback must not force the independent desktop leg to TCP');
 assert(fallbacks.every(server=>server.urls.every(url=>!url.includes('transport=udp'))));
}
console.log('Verified same-endpoint UDP-first browser candidates, IPv6/plain/TLS URI safety, retained TCP/TLS fallbacks and unchanged API credentials. No media or network was used.');

{
 const deferred=()=>{let resolve,reject;const promise=new Promise((ok,fail)=>{resolve=ok;reject=fail});return {promise,resolve,reject}};
 function stream(){const make=()=>({kind:'audio',readyState:'live',stops:0,listeners:{},stop(){this.stops++;this.readyState='ended'},addEventListener(type,callback){this.listeners[type]=callback}}),tracks=[make(),make()];return {tracks,getTracks:()=>tracks,getAudioTracks:()=>tracks}}
 function ready(viewer){viewer.channels.filter(channel=>channel.label==='input').at(-1).onmessage({data:JSON.stringify({type:'pong',time:0,microphoneReady:true})})}
 function sender(viewer){return viewer.peers.at(-1).transceivers.find(transceiver=>transceiver.kind==='audio').sender}
 function permission(viewer){const requests=[];viewer.navigator.mediaDevices={getUserMedia(){const request=deferred();requests.push(request);return request.promise}};ready(viewer);return requests}
 {
  const viewer=await start(true),requests=permission(viewer),button=viewer.nodes.microphone;
  const first=button.onclick();await button.onclick();assert.equal(requests.length,1,'Repeated taps must not open competing microphone requests');assert.equal(button.disabled,true);
  const acquired=stream();requests[0].resolve(acquired);await first;
  assert.equal(sender(viewer).replaceCalls[0],acquired.tracks[0]);assert.equal(button.attributes['aria-pressed'],'true');assert.equal(button.disabled,false);
  await button.onclick();assert(acquired.tracks.every(track=>track.stops>0),'Turning off must stop every acquired track');assert.equal(sender(viewer).replaceCalls.at(-1),null);assert.equal(button.attributes['aria-pressed'],'false');
 }
 {
  const viewer=await start(true),requests=permission(viewer),button=viewer.nodes.microphone,oldSender=sender(viewer),first=button.onclick();
  await viewer.nodes.stop.onclick();const acquired=stream();requests[0].resolve(acquired);await first;
  assert(acquired.tracks.every(track=>track.stops>0));assert.equal(oldSender.replaceCalls.length,0,'A permission result received after disconnect must never attach');assert.equal(button.disabled,false);assert.equal(button.attributes['aria-pressed'],'false');
  assert(!viewer.sent.some(event=>event.type==='microphoneState'&&event.enabled));
 }
 {
  const viewer=await start(true),requests=permission(viewer),button=viewer.nodes.microphone,oldSender=sender(viewer),oldRequest=button.onclick();
  viewer.nodes.connect.onclick();await flush();ready(viewer);const newSender=sender(viewer),newRequest=button.onclick();assert.notEqual(newSender,oldSender);assert.equal(requests.length,2);
  const obsolete=stream();requests[0].resolve(obsolete);await oldRequest;
  assert(obsolete.tracks.every(track=>track.stops>0));assert.equal(oldSender.replaceCalls.length,0);assert.equal(newSender.replaceCalls.length,0,'Old permission must not attach to the new session sender');assert.equal(button.disabled,true,'An old completion must not unlock a new pending request');
  const current=stream();requests[1].resolve(current);await newRequest;assert.equal(newSender.replaceCalls[0],current.tracks[0]);assert.equal(button.attributes['aria-pressed'],'true');assert.equal(button.disabled,false);
  current.tracks[0].readyState='ended';current.tracks[0].listeners.ended();await flush();assert(current.tracks.every(track=>track.stops>0));assert.equal(button.attributes['aria-pressed'],'false');assert.equal(viewer.sent.filter(event=>event.type==='microphoneState').at(-1).enabled,false,'Device revocation releases the virtual microphone route');
 }
 {
  const viewer=await start(true),requests=permission(viewer),button=viewer.nodes.microphone,oldSender=sender(viewer),attached=deferred();oldSender.replaceHandler=()=>attached.promise;
  const pending=button.onclick(),acquired=stream();requests[0].resolve(acquired);await flush();assert.equal(oldSender.replaceCalls[0],acquired.tracks[0]);
  await viewer.nodes.stop.onclick();assert(acquired.tracks.every(track=>track.stops>0),'Disconnect stops devices while replaceTrack is pending');attached.resolve();await pending;
  assert(!viewer.sent.some(event=>event.type==='microphoneState'&&event.enabled));assert.equal(button.attributes['aria-pressed'],'false');assert.equal(button.disabled,false);
 }
 {
  const viewer=await start(true),requests=permission(viewer),button=viewer.nodes.microphone;sender(viewer).replaceHandler=async()=>{throw new Error('Synthetic replaceTrack failure')};
  const pending=button.onclick(),acquired=stream();requests[0].resolve(acquired);await pending;assert(acquired.tracks.every(track=>track.stops>0));assert.equal(button.attributes['aria-pressed'],'false');assert.equal(button.disabled,false);
 }
 console.log('Verified serialized microphone permission, disconnect/reconnect generation fencing, pending attachment cleanup, track revocation and replaceTrack failure. No microphone or network was opened.');
}
}

{
 const pages=await start(true,scripts,async(path)=>path.endsWith('/version.json')?{ok:true,json:async()=>({build:21})}:{ok:true,status:200,json:async()=>({type:'answer',sdp:'answer-sdp'})},undefined,19,undefined,false,undefined,'tycofficial.github.io');
 assert(pages.requests.some(request=>request.path.endsWith('/version.json')));
 assert.equal(pages.navigations.length,1,'GitHub Pages must see its own build even when the signaling Site reports an older version');
 assert.match(pages.navigations[0],/_release=21/);
 console.log('Verified GitHub Pages release checking independent of the signaling Site version.');
}
