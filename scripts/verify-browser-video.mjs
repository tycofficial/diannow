// Real Chromium WebRTC receiver; no microphone capture, audio playback or input.
// Uses an isolated temporary browser profile and disconnects only its own peer.
// Node 24: use --use-env-proxy where signaling requires the existing proxy.
import {spawn} from 'node:child_process';
import {mkdir,readFile} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

const options=Object.fromEntries(process.argv.slice(2).map(arg=>arg.replace(/^--/,'').split('=')));
const seconds=Number(options.seconds||30),route=options.route||'udp';
if(!Number.isInteger(seconds)||seconds<5||seconds>120||!['direct','udp','tcp'].includes(route))throw new Error('Use --seconds=5..120 --route=direct|udp|tcp');
const qualities=(options.qualities||'480,1080').split(',');
if(!qualities.length||qualities.length>3||qualities.some(value=>!['480','720','1080'].includes(value)))throw new Error('Use supported profiles, at most three');
if(options['host-transport']&&!['udp','tcp'].includes(options['host-transport']))throw new Error('Unsupported desktop transport');
const failures=[];
const stateSource=(await readFile(new URL('../web/session-state.mjs',import.meta.url),'utf8')).replaceAll('export class','class');
const root=process.env.DIANNOW_PROBE_DIR||path.join(process.env.TEMP||'.','diannow-browser-probe');
const profile=path.join(root,'profile-'+Date.now());await mkdir(profile,{recursive:true});
const browserPath=options.browser||'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const browser=spawn(browserPath,['--headless=new','--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','--user-data-dir='+profile,'--proxy-server=http://127.0.0.1:7890','--no-first-run','--no-default-browser-check','--disable-extensions','--disable-background-timer-throttling','--disable-renderer-backgrounding','--disable-backgrounding-occluded-windows','--autoplay-policy=no-user-gesture-required','about:blank'],{windowsHide:true,stdio:['ignore','ignore','pipe']});
let socket,id=0,session,exitPromise=new Promise(resolve=>browser.once('exit',resolve));const pending=new Map();
async function call(method,params={},target=session){
  const key=++id;return await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{pending.delete(key);reject(new Error('CDP timeout: '+method))},45000);
    pending.set(key,{resolve:value=>{clearTimeout(timer);resolve(value)},reject:error=>{clearTimeout(timer);reject(error)}});
    socket.send(JSON.stringify({id:key,method,params,...(target?{sessionId:target}:{})}));
  });
}
async function evaluate(expression){const value=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(value.exceptionDetails)throw new Error(value.exceptionDetails.exception?.description||value.exceptionDetails.text);return value.result?.value}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
try{
  const endpoint=await new Promise((resolve,reject)=>{
    let stderr='';const timer=setTimeout(()=>reject(new Error('Browser launch timeout')),20000);
    browser.stderr.on('data',chunk=>{stderr+=chunk;const match=/DevTools listening on (ws:\/\/[^\s]+)/.exec(stderr);if(match){clearTimeout(timer);resolve(match[1])}if(stderr.length>8192)stderr=stderr.slice(-4096)});
    browser.once('error',error=>{clearTimeout(timer);reject(error)});
  });
  socket=new WebSocket(endpoint);await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject});
  socket.onmessage=event=>{const value=JSON.parse(event.data),task=pending.get(value.id);if(task){pending.delete(value.id);if(value.error)task.reject(new Error(value.error.message));else task.resolve(value.result)}else if(value.method==='Runtime.bindingCalled')void bridge(value.params.payload)};
  async function bridge(payload){
    const request=JSON.parse(payload);let result;
    try{
      if(!/^\/api\/(?:ice|offer|answer\/[a-f0-9-]+)$/.test(request.path))throw new Error('Unexpected probe path');
      const response=await fetch('https://desktop-live-tianw-1105.tyc130923.chatgpt.site'+request.path,{method:request.body?'POST':'GET',headers:{'content-type':'application/json'},...(request.body?{body:JSON.stringify(request.body)}:{}),signal:AbortSignal.timeout(12000)});
      result={status:response.status,body:await response.text()};
    }catch(error){result={error:error.message}}
    await call('Runtime.evaluate',{expression:`window.signalReplies.get(${request.id})(${JSON.stringify(result)});window.signalReplies.delete(${request.id});void 0`}).catch(()=>{});
  }
  const {targetId}=await call('Target.createTarget',{url:'about:blank'},null);({sessionId:session}=await call('Target.attachToTarget',{targetId,flatten:true},null));
  await call('Page.enable');await call('Runtime.enable');await call('Runtime.addBinding',{name:'probeSignal'});
  await evaluate(`(async()=>{
    ${stateSource}
    document.body.innerHTML='<video id="probe" autoplay muted playsinline style="width:960px;height:540px"></video>';
    const route=${JSON.stringify(route)};window.signalReplies=new Map();let requestId=0;
    async function signal(path,body){const id=++requestId;const reply=await new Promise(resolve=>{signalReplies.set(id,resolve);probeSignal(JSON.stringify({id,path,body}))});if(reply.error)throw new Error(reply.error);return {status:reply.status,ok:reply.status>=200&&reply.status<300,json:()=>JSON.parse(reply.body)}};
    const config=(await signal('/api/ice')).json();
    const servers=route==='direct'?[]:config.iceServers.flatMap(server=>{
      const urls=(Array.isArray(server.urls)?server.urls:[server.urls]).filter(url=>typeof url==='string'&&url.startsWith('turn:')).map(url=>url.split('?')[0]+'?transport='+route);
      return urls.length?[{...server,urls:[...new Set(urls)]}]:[];
    });
    const peer=new RTCPeerConnection({iceServers:servers,iceTransportPolicy:route==='direct'?'all':'relay',bundlePolicy:'balanced'});
    const channel=peer.createDataChannel('input'),diagnosticChannel=peer.createDataChannel('pointer',{ordered:false,maxRetransmits:0}),stateChannel=peer.createDataChannel('state',{ordered:false,maxRetransmits:0}),transceiver=peer.addTransceiver('video',{direction:'recvonly'}),video=document.getElementById('probe');
    window.probe={peer,channel,diagnosticChannel,stateChannel,video,settingsAck:null,stateTimeout:null,rtts:[],routineBytes:[],diagnostics:null,actualDisplay:null,source:[],presented:[],route};
    probe.stateSync=new SessionStateSync({writeState:event=>{if(stateChannel.readyState!=='open'||stateChannel.bufferedAmount>=2048)return false;stateChannel.send(JSON.stringify(event));return true},writeLegacy:()=>false,onTimeout:state=>{probe.stateTimeout=state.type}});
    const frame=(now,meta)=>{probe.presented.push([now,meta.presentedFrames]);if(probe.presented.length>15000)probe.presented.splice(0,5000);video.requestVideoFrameCallback(frame)};video.requestVideoFrameCallback(frame);
    peer.ontrack=event=>{video.srcObject=new MediaStream([event.track]);event.receiver.jitterBufferTarget=0;video.play().catch(()=>{})};
    channel.onmessage=diagnosticChannel.onmessage=stateChannel.onmessage=event=>{const value=JSON.parse(event.data);if(value.type==='stateAck'){if(probe.stateSync.acknowledge(value)&&value.stateType==='settings')probe.settingsAck={...value,at:performance.now()}}else if(value.type==='pong'){if(value.display)probe.actualDisplay=value.display;if(value.stateProtocol===1)probe.stateSync.enable();if(value.videoDiagnostics)probe.diagnostics=value.videoDiagnostics;else{probe.rtts.push(performance.now()-value.time);probe.routineBytes.push(new TextEncoder().encode(event.data).length)}probe.source.push(value.fps)}};
    await peer.setLocalDescription(await peer.createOffer());
    await new Promise(resolve=>{if(peer.iceGatheringState==='complete')resolve();else{peer.addEventListener('icegatheringstatechange',()=>{if(peer.iceGatheringState==='complete')resolve()});setTimeout(resolve,6000)}});
    const body={viewerId:'native-probe-'+${JSON.stringify(randomUUID())},viewerVersion:'native-probe-v22',type:'offer',sdp:peer.localDescription.sdp,separateControl:true,preferences:{display:'original',quality:${JSON.stringify(qualities[0])},fps:60,fixedProfile:true,desktopRelayTransport:${JSON.stringify(options['host-transport']||null)}}};
    const offered=await signal('/api/offer',body);if(!offered.ok)throw new Error('Offer failed '+offered.status);const {id}=offered.json();
    let answer;for(let i=0;i<65;i++){const response=await signal('/api/answer/'+id);if(response.status===200){answer=response.json();break}await new Promise(resolve=>setTimeout(resolve,400))}if(!answer)throw new Error('Answer timeout');
    if(route!=='direct')answer.sdp=answer.sdp.split('\\r\\n').filter(line=>!line.startsWith('a=candidate:')||line.includes(' typ relay')).join('\\r\\n');
    await peer.setRemoteDescription(answer);
    return true;
  })()`);
  for(let i=0;i<250;i++){if(await evaluate("probe.channel.readyState==='open'"))break;await sleep(100)}
  if(!await evaluate("probe.channel.readyState==='open'"))throw new Error('Media/control connection timeout');
  for(let i=0;i<30;i++){if(await evaluate("if(probe.stateChannel.readyState==='open')probe.stateChannel.send(JSON.stringify({type:'ping',time:performance.now()}));probe.stateSync.supported"))break;await sleep(200)}
  if(!await evaluate('probe.stateSync.supported'))throw new Error('Backend did not negotiate the acknowledged session-state protocol');
  for(const quality of qualities){
    await evaluate(`probe.settingsAck=null;probe.stateTimeout=null;probe.settingStart=performance.now();probe.stateSync.send({type:'settings',quality:${JSON.stringify(quality)},fps:60,fixedProfile:true});probe.settingRevision=probe.stateSync.desired.get('settings').revision;true`);
    let applied;
    for(let attempt=0;attempt<100;attempt++){
      applied=await evaluate(`({ack:probe.settingsAck,revision:probe.settingRevision,width:probe.video.videoWidth,height:probe.video.videoHeight,elapsed:performance.now()-probe.settingStart,attempts:probe.stateSync.desired.get('settings').attempts,timeout:probe.stateTimeout,connected:probe.peer.connectionState==='connected',actualDisplay:probe.actualDisplay})`);
      if(applied.ack?.revision===applied.revision&&applied.height===Number(quality))break;
      if(applied.timeout||!applied.connected)break;
      await sleep(200);
    }
    if(applied.ack?.revision!==applied.revision||applied.height!==Number(quality)){
      const problem='settings ACK or matching decoded resolution timed out';failures.push(quality+'p: '+problem);
      console.log(JSON.stringify({phaseResult:{route,quality,dimensions:[applied.width,applied.height],actualDisplay:applied.actualDisplay,settingsApplied:applied,acceptancePassed:false,acceptanceFailures:[problem],browserReceiver:true,phoneNetworkMeasured:false}}));continue;
    }
    console.log(JSON.stringify({route,quality,settingsAppliedMs:applied.elapsed,settingsAttempts:applied.attempts,settingsRevision:applied.revision,dimensions:[applied.width,applied.height]}));
    await evaluate(`probe.phase=performance.now();probe.rtts=[];probe.routineBytes=[];probe.source=[];probe.previousInbound=null;probe.lastReceiverReport=0;true`);
    await sleep(3000);let previous,intervals=[],first,last,phaseError=null;
    for(let i=0;i<=seconds;i++){
      let sample;try{sample=await evaluate(`(async()=>{if(${i%5===0}&&probe.diagnosticChannel.readyState==='open')probe.diagnosticChannel.send(JSON.stringify({type:'ping',time:performance.now(),diagnostics:true}));let stats;try{stats=await probe.peer.getStats()}catch(error){return {time:performance.now(),state:probe.peer.connectionState,controlState:probe.channel.readyState,stateChannelState:probe.stateChannel.readyState,statsValid:false,statsError:error.message}}const video=[...stats.values()].find(x=>x.type==='inbound-rtp'&&x.kind==='video');const pair=[...stats.values()].find(x=>x.type==='candidate-pair'&&x.state==='succeeded'&&x.nominated);const previous=probe.previousInbound,ping={type:'ping',time:performance.now()};if(video&&previous){const elapsed=(video.timestamp-previous.timestamp)/1000,emitted=video.jitterBufferEmittedCount-previous.jitterBufferEmittedCount,delay=video.jitterBufferDelay-previous.jitterBufferDelay;const report={viewerVisible:1,videoRequestedEnabled:1,lossDelta:Math.max(0,video.packetsLost-previous.packetsLost)};if(elapsed>0)report.fps=(video.framesDecoded-previous.framesDecoded)/elapsed;if(emitted>0&&delay>=0)report.bufferMs=delay/emitted*1000;if(ping.time-probe.lastReceiverReport>=2000){ping.receiverReport=report;probe.lastReceiverReport=ping.time}}probe.previousInbound=video;const heartbeat=probe.stateChannel.readyState==='open'?probe.stateChannel:probe.channel;if(heartbeat.readyState==='open')heartbeat.send(JSON.stringify(ping));return {time:performance.now(),statsValid:!!video&&Number.isFinite(video.framesDecoded),controlState:probe.channel.readyState,stateChannelState:probe.stateChannel.readyState,frames:video?.framesDecoded||0,received:video?.framesReceived||0,width:video?.frameWidth,height:video?.frameHeight,lost:video?.packetsLost,packets:video?.packetsReceived,bytes:video?.bytesReceived,freezeCount:video?.freezeCount,freezeSeconds:video?.totalFreezesDuration,jitterDelay:video?.jitterBufferDelay,jitterEmitted:video?.jitterBufferEmittedCount,nack:video?.nackCount,pli:video?.pliCount,pairRtt:pair?.currentRoundTripTime,state:probe.peer.connectionState}})()`)}catch(error){phaseError='receiver sampling failed: '+error.message;break}
      if(!sample.statsValid){phaseError='receiver statistics unavailable: '+(sample.statsError||sample.state);break}
      first??=sample;last=sample;if(previous)intervals.push((sample.frames-previous.frames)*1000/(sample.time-previous.time));previous=sample;
      if(i&&i%5===0)console.log(JSON.stringify({route,quality,elapsed:i,decodedFps:Number(intervals.at(-1).toFixed(2))}));
      if(sample.state!=='connected'||sample.controlState!=='open'||sample.stateChannelState!=='open'){phaseError='connection or control channel closed during measurement';break}
      if(i<seconds)await sleep(1000);
    }
    const details=await evaluate('({diagnostics:probe.diagnostics,actualDisplay:probe.actualDisplay,rtts:probe.rtts,routineBytes:probe.routineBytes,source:probe.source,presented:probe.presented.filter(x=>x[0]>=probe.phase+3000)})').catch(()=>({diagnostics:null,actualDisplay:applied.actualDisplay,rtts:[],routineBytes:[],source:[],presented:[]}));
    if(!first||!last){const problem=phaseError||'no receiver samples collected';failures.push(quality+'p: '+problem);console.log(JSON.stringify({phaseResult:{route,quality,requestedSeconds:seconds,seconds:0,completedIntervals:0,actualDisplay:details.actualDisplay,acceptancePassed:false,acceptanceFailures:[problem],browserReceiver:true,phoneNetworkMeasured:false}}));break}
    const gaps=details.presented.slice(1).map((value,index)=>value[0]-details.presented[index][0]);const median=values=>values.length?values.sort((a,b)=>a-b)[Math.floor(values.length/2)]:null;
    const result={route,quality,requestedSeconds:seconds,completedIntervals:intervals.length,actualDisplay:details.actualDisplay,connectionState:last.state,controlState:last.controlState,stateChannelState:last.stateChannelState,receiverReportIntervalMs:2000,settingsAppliedMs:applied.elapsed,settingsAttempts:applied.attempts,seconds:(last.time-first.time)/1000,dimensions:[last.width,last.height],averageDecodedFps:(last.frames-first.frames)*1000/(last.time-first.time),minimumOneSecondFps:Math.min(...intervals),zeroFpsIntervals:intervals.filter(x=>x<1).length,freezeCount:last.freezeCount-first.freezeCount,freezeSeconds:last.freezeSeconds-first.freezeSeconds,maximumPresentedGapMs:gaps.length?Math.max(...gaps):null,receivedMbps:8*(last.bytes-first.bytes)/(last.time-first.time)/1000,packetsLost:last.lost-first.lost,nackCount:last.nack-first.nack,pliCount:last.pli-first.pli,medianControlRttMs:median(details.rtts),maximumRoutineHeartbeatBytes:details.routineBytes.length?Math.max(...details.routineBytes):null,reportedSourceFps:median(details.source),videoDiagnostics:details.diagnostics,browserReceiver:true,phoneNetworkMeasured:false,desktopInput:false,audioPlayback:false};
    const firstPresentation=details.presented[0],lastPresentation=details.presented.at(-1);
    result.averagePresentedFps=lastPresentation&&lastPresentation[0]>firstPresentation[0]?(lastPresentation[1]-firstPresentation[1])*1000/(lastPresentation[0]-firstPresentation[0]):0;
    const problems=[];
    if(phaseError)problems.push(phaseError);
    if(intervals.length!==seconds)problems.push('incomplete measurement: '+intervals.length+'/'+seconds+' one-second intervals');
    if(details.actualDisplay?.virtual!==false||details.actualDisplay?.mode!=='original')problems.push('actual capture is not confirmed as the original desktop');
    if(last.state!=='connected')problems.push('connection lost');
    if(last.height!==Number(quality))problems.push('wrong resolution');
    if(result.averageDecodedFps<58)problems.push('average decoded FPS below 58');
    if(result.minimumOneSecondFps<50)problems.push('one-second FPS below 50');
    if(result.averagePresentedFps<58)problems.push('average presented FPS below 58');
    if(result.maximumPresentedGapMs===null||result.maximumPresentedGapMs>100)problems.push('presentation gap above 100ms or unavailable');
    if(result.freezeCount>0||result.zeroFpsIntervals>0)problems.push('video froze');
    result.acceptancePassed=!problems.length;result.acceptanceFailures=problems;
    failures.push(...problems.map(problem=>quality+'p: '+problem));
    console.log(JSON.stringify({phaseResult:result}));
    if(phaseError)break;
  }
  console.log(JSON.stringify({acceptancePassed:!failures.length,acceptanceFailures:failures,phoneNetworkMeasured:false}));
  if(failures.length)process.exitCode=1;
}finally{
  if(socket?.readyState===1){try{await evaluate("if(window.probe){probe.stateSync.reset();if(probe.channel.readyState==='open')probe.channel.send(JSON.stringify({type:'disconnect'}));probe.peer.close()}true")}catch{}try{await call('Browser.close',{},null)}catch{}socket.close()}
  await Promise.race([exitPromise,sleep(5000)]);if(browser.exitCode===null)browser.kill();
}
