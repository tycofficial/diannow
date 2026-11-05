import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import worker from '../worker/index.js';
const values=new Map(),listCalls=[],viewerKey='test-viewer-key',agentKey='test-agent-key';
const hash=value=>createHash('sha256').update(value).digest('hex');
const env={VIEWER_KEY_SHA256:hash(viewerKey),AGENT_KEY_SHA256:hash(agentKey),FRONTEND_ORIGINS:'https://tycofficial.github.io',BUCKET:{
 async put(key,value){values.set(key,value)},async get(key){return values.has(key)?{json:async()=>JSON.parse(values.get(key))}:null},async delete(key){values.delete(key)},
 async list(options){
  listCalls.push(options);
  assert.equal(options.prefix,'rtc/pending/');assert(options.limit>0&&options.limit<=1000);
  const keys=[...values.keys()].filter(key=>key.startsWith(options.prefix)&&(!options.cursor||key>options.cursor)).sort();
  const selected=keys.slice(0,options.limit),truncated=keys.length>selected.length;
  return {objects:selected.map(key=>({key})),truncated,...(truncated?{cursor:selected.at(-1)}:{})};
 }
}};
const call=(path,method='GET',role=null,body=null,origin=null)=>{
 const headers={};if(role==='viewer')headers.Authorization='Bearer '+viewerKey;if(role==='agent')headers['X-Agent-Key']=agentKey;if(origin)headers.Origin=origin;
 return worker.fetch(new Request('https://screen.example'+path,{method,headers,...(body?{body:JSON.stringify(body)}:{})}),env);
};
assert.equal((await call('/')).status,200);
let response=await call('/api/offer','POST',null,{type:'offer',sdp:'v=0'});assert.equal(response.status,200);const {id}=await response.json();
assert.equal((await call('/api/agent/poll','GET','viewer')).status,403);
assert.equal((await (await call('/api/agent/poll','GET','agent')).json()).id,id);
assert.equal((await call('/api/answer/'+id,'POST','viewer',{type:'answer',sdp:'v=0'})).status,403);
assert.equal((await call('/api/answer/'+id,'POST','agent',{type:'answer',sdp:'v=0'})).status,200);
assert.equal((await call('/api/answer/'+id)).status,200);
assert.equal((await (await call('/api/answer/'+id,'GET','viewer')).json()).type,'answer');
assert.equal((await call('/api/offer','POST','viewer',{type:'offer',sdp:'v=0'},'https://other.example')).status,403);
response=await call('/api/offer','OPTIONS',null,null,'https://tycofficial.github.io');assert.equal(response.status,204);assert.equal(response.headers.get('access-control-allow-origin'),'https://tycofficial.github.io');
assert.equal((await call('/api/agent/poll','GET','viewer')).status,403);

// Separate viewers must remain pending until their own answer is published.
values.clear();
const offers=await Promise.all(['first-viewer','second-viewer'].map(async sdp=>(await call('/api/offer','POST',null,{type:'offer',sdp})).json()));
const expected=new Set(offers.map(offer=>offer.id)),answered=new Set();
for(let i=0;i<offers.length;i++){
 const pending=await (await call('/api/agent/poll','GET','agent')).json();
 assert(expected.has(pending.id),'Every queued offer must remain available');
 assert(!answered.has(pending.id),'An answered offer must not be returned again');
 assert.equal((await call('/api/answer/'+pending.id,'POST','agent',{type:'answer',sdp:'reply-'+pending.offer.sdp})).status,200);
 answered.add(pending.id);
}
for(const offer of offers)assert.equal((await call('/api/answer/'+offer.id)).status,200);
assert.deepEqual(await (await call('/api/agent/poll','GET','agent')).json(),{});

// Expired items cannot block later pages in the R2 listing.
values.clear();listCalls.length=0;
for(let i=0;i<40;i++){
 const expiredId='00000000-0000-4000-8000-'+String(i).padStart(12,'0');
 values.set('rtc/pending/'+expiredId,JSON.stringify({id:expiredId,offer:{type:'offer',sdp:'expired'},createdAt:Date.now()-61000}));
}
const active=await (await call('/api/offer','POST',null,{type:'offer',sdp:'active'})).json();
assert.equal((await (await call('/api/agent/poll','GET','agent')).json()).id,active.id);
assert(listCalls.length>=2,'Polling must continue after an expired listing page');
assert.equal([...values.keys()].filter(key=>key.startsWith('rtc/pending/')).length,1);

// A completed legacy single-slot offer must never starve the new queue.
const legacyId='ffffffff-ffff-4fff-8fff-ffffffffffff';
values.set('rtc/pending',JSON.stringify({id:legacyId,offer:{type:'offer',sdp:'legacy'},createdAt:Date.now()}));
values.set('rtc/answer/'+legacyId,JSON.stringify({type:'answer',sdp:'legacy-answer',createdAt:Date.now()}));
await call('/api/answer/'+active.id,'POST','agent',{type:'answer',sdp:'active-answer'});
assert.deepEqual(await (await call('/api/agent/poll','GET','agent')).json(),{});
assert(!values.has('rtc/pending'));

// Pending legacy requests are still served during deployment migration.
values.set('rtc/pending',JSON.stringify({id:legacyId,offer:{type:'offer',sdp:'legacy'},createdAt:Date.now()}));
values.delete('rtc/answer/'+legacyId);
assert.equal((await (await call('/api/agent/poll','GET','agent')).json()).id,legacyId);
await call('/api/answer/'+legacyId,'POST','agent',{type:'answer',sdp:'legacy-answer'});
assert.deepEqual(await (await call('/api/agent/poll','GET','agent')).json(),{});
console.log('Signaling checks passed: concurrent viewers, completed and expired queue cleanup, legacy migration, anonymous viewing, agent access and Pages CORS. No network traffic.');
