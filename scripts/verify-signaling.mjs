import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import worker from '../worker/index.js';
const values=new Map(),viewerKey='test-viewer-key',agentKey='test-agent-key';
const hash=value=>createHash('sha256').update(value).digest('hex');
const env={VIEWER_KEY_SHA256:hash(viewerKey),AGENT_KEY_SHA256:hash(agentKey),FRONTEND_ORIGINS:'https://tycofficial.github.io',BUCKET:{
 async put(key,value){values.set(key,value)},async get(key){return values.has(key)?{json:async()=>JSON.parse(values.get(key))}:null},async delete(key){values.delete(key)}
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
console.log('Signaling checks passed: anonymous viewing, separated agent access, session exchange and Pages CORS.');
