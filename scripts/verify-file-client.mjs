import assert from 'node:assert/strict';
import {createHash,randomBytes} from 'node:crypto';
import {FileSha256,FileTransferClient} from '../web/file-transfer.mjs';
for(const size of [0,1,55,56,63,64,65,127,1024,61440,1048583]){const data=randomBytes(size);for(const stride of [1,97,61440]){const hash=new FileSha256();for(let offset=0;offset<size;offset+=stride)hash.update(data.subarray(offset,offset+stride));assert.equal(hash.hex(),createHash('sha256').update(data).digest('hex'));assert.equal(hash.hex(),hash.hex())}}
// Native hash vectors above; protocol channel below is deliberately a double.
const bytes=randomBytes(5*1024*1024+3),expected=createHash('sha256').update(bytes).digest('hex');let received=0,finish=0,post=0,answerGets=0;
const hash=createHash('sha256');const identity='a'.repeat(32),token='b'.repeat(43);let client;
class Peer{
 constructor(){this.iceGatheringState='complete';this.connectionState='connected'}
 createDataChannel(){this.channel={readyState:'open',bufferedAmount:0,send:data=>{if(typeof data==='string'){const e=JSON.parse(data);let value;if(e.type==='hello')value={type:'ready',protocol:1,chunkBytes:61440,windowBytes:4194304,maxFileBytes:128*1024**3};if(e.type==='begin')value={type:'accepted',id:identity,token,committed:0,status:'receiving',prefixSha256:createHash('sha256').digest('hex')};if(e.type==='finish'){finish++;assert.equal(e.sha256,expected);assert.equal(hash.digest('hex'),expected);value={type:'complete',id:identity,sha256:expected,committed:bytes.length,status:'complete',path:'D:\\DIANNOW\\Received\\no_extension'}}queueMicrotask(()=>this.channel.onmessage({data:JSON.stringify({...value,requestId:e.requestId})}))}else{const packet=new Uint8Array(data),offset=Number(new DataView(packet.buffer).getBigUint64(20));assert.equal(offset,received);assert(packet.length<=61468);hash.update(packet.subarray(28));received+=packet.length-28;queueMicrotask(()=>this.channel.onmessage({data:JSON.stringify({type:'status',id:identity,committed:received,status:'receiving'})}))}}};return this.channel}
 async createOffer(){return {sdp:'synthetic-file-only',type:'offer'}}
 async setLocalDescription(value){this.localDescription=value}
 async setRemoteDescription(){}
 close(){}
}
client=new FileTransferClient({Peer,storage:null,fetcher:async(url,options)=>{if(url.endsWith('/api/offer')){post++;assert.equal(JSON.parse(options.body).purpose,'file-transfer');return {ok:true,json:async()=>({id:'synthetic'})}}answerGets++;return answerGets===1?{status:204}:{status:200,json:async()=>({sdp:'synthetic',type:'answer'})}}});
const file=new File([bytes],'no_extension');client.add([file]);for(let i=0;i<400&&client.running;i++)await new Promise(resolve=>setTimeout(resolve,10));assert.equal(client.tasks[0].status,'complete');assert.equal(received,bytes.length);assert.equal(post,1);assert.equal(answerGets,2);assert.equal(finish,1);client.close();assert.equal(client.pending.size,0);assert.equal(client.requests.size,0);
console.log('PASS native SHA256 vectors; client 5MiB chunk/backpressure/204 polling/one POST/receiver completion (channel double)');
