import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const html=await readFile('docs/index.html','utf8');
const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match=>match[1]);
assert.equal(scripts.length,2);
async function start(rtcAvailable){
  const nodes={},stages=[],requests=[];
  for(const [,id] of html.matchAll(/id="([^"]+)"/g)){
    let open=false;
    nodes[id]={textContent:'',value:id==='quality'?'auto':'60',options:[],
      classList:{toggle(){open=!open;return open}},setAttribute(){},appendChild(){},addEventListener(){},focus(){},blur(){}};
  }
  class Peer{
    iceGatheringState='complete';connectionState='new';
    createDataChannel(){return {readyState:'open',bufferedAmount:0,send(){}}}
    addTransceiver(){return {sender:{}}}
    async createOffer(){return {type:'offer',sdp:'test-sdp'}}
    async setLocalDescription(value){this.localDescription=value}
    async setRemoteDescription(){this.connectionState='connected';this.onconnectionstatechange()}
    close(){}
  }
  const window={addEventListener(){},isSecureContext:true};window.self=window;window.top=window;
  const context=vm.createContext({window,document:{getElementById:id=>nodes[id],querySelectorAll:()=>[],createElement:()=>({}),addEventListener(){}},
    location:{hostname:'screen.example'},setTimeout:()=>1,clearTimeout(){},setInterval(){},requestAnimationFrame(){},
    AbortController,MediaStream:class{},RTCPeerConnection:rtcAvailable?Peer:undefined,
    XMLHttpRequest:class{open(){}setRequestHeader(){}send(value){stages.push(JSON.parse(value).stage)}},
    fetch:async(path,options)=>{requests.push({path,options});return {ok:true,status:200,json:async()=>({type:'answer',sdp:'answer-sdp'})}}});
  // Reproduce a host that already has common top-level names. Viewer must stay scoped.
  vm.runInContext('const video=1, keys=2, pc=3;',context);
  for(const script of scripts)vm.runInContext(script,context);
  for(let i=0;i<15;i++)await Promise.resolve();
  nodes.keyboard.onclick();
  assert.equal(nodes.keyboard.textContent,'收起键盘');
  return {nodes,stages,requests};
}
const connected=await start(true);
assert(connected.stages.includes('connected'));
assert.equal(JSON.parse(connected.requests[0].options.body).preferences.quality,'720');
assert.equal(connected.requests[0].options.credentials,'omit');
const unavailable=await start(false);
assert(unavailable.stages.includes('connection-error'));
assert.match(unavailable.nodes.overlay.textContent,/没有提供/);
console.log('Viewer startup verified: host-name collisions, keyboard binding, SDP request, connected state and missing-RTC error. No media was transmitted.');
