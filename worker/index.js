const page = "__VIEWER_HTML__";
const baseHeaders = {'cache-control':'no-store, private','content-type':'application/json','x-content-type-options':'nosniff','referrer-policy':'no-referrer','x-robots-tag':'noindex, nofollow'};
const idPattern = /^[0-9a-f-]{36}$/;

async function matchesHash(value, expected) {
  if(typeof value!=='string'||!value||value.length>512||!expected)return false;
  const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value));
  const actual=Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
  if(actual.length!==expected.length)return false;
  let difference=0;for(let i=0;i<actual.length;i++)difference|=actual.charCodeAt(i)^expected.charCodeAt(i);
  return difference===0;
}

async function storageRead(bucket,key){for(let attempt=0;;attempt++){try{return await bucket.get(key)}catch(error){if(attempt>=2)throw error;await new Promise(resolve=>setTimeout(resolve,150*(attempt+1)));}}}

export default {
  async fetch(request, env) {
    const url=new URL(request.url),path=url.pathname,origin=request.headers.get('origin');
    const allowedOrigins=new Set([url.origin,...(env.FRONTEND_ORIGINS||'https://tycofficial.github.io').split(',').map(value=>value.trim()).filter(Boolean)]);
    const headers={...baseHeaders};
    if(origin&&allowedOrigins.has(origin))Object.assign(headers,{'access-control-allow-origin':origin,'vary':'Origin','access-control-allow-methods':'GET, POST, DELETE, OPTIONS','access-control-allow-headers':'Authorization, Content-Type, X-Agent-Key, X-Viewer-Key','access-control-max-age':'600'});
    const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers});
    if(request.method==='OPTIONS')return new Response(null,{status:origin&&allowedOrigins.has(origin)?204:403,headers});
    if(path==='/'&&request.method==='GET')return new Response(page,{headers:{...headers,'content-type':'text/html; charset=utf-8'}});
    if(path.startsWith('/s/')&&request.method==='GET')return Response.redirect(url.origin+'/',302);
    if(!path.startsWith('/api/'))return json({error:'Not found'},404);
    if(origin&&!allowedOrigins.has(origin))return json({error:'Origin is not allowed.'},403);
    if(!env.BUCKET)return json({error:'Signaling storage is not ready.'},503);
    const isAgentOperation=path==='/api/agent/poll'||(path.startsWith('/api/answer/')&&request.method!=='GET');
    if(isAgentOperation&&!await matchesHash(request.headers.get('x-agent-key'),env.AGENT_KEY_SHA256))return json({error:'Agent authorization failed.'},403);

    if(path==='/api/offer'&&request.method==='POST'){
      if(Number(request.headers.get('content-length'))>100000)return json({error:'Offer is too large.'},413);
      let offer;try{offer=await request.json()}catch{return json({error:'Invalid offer'},400)}
      if(offer.type!=='offer'||typeof offer.sdp!=='string'||offer.sdp.length>100000)return json({error:'Invalid offer'},400);
      const id=crypto.randomUUID(),createdAt=Date.now();
      await env.BUCKET.put('rtc/pending',JSON.stringify({id,offer,createdAt}),{httpMetadata:{contentType:'application/json'}});
      return json({id});
    }
    if(path==='/api/agent/poll'&&request.method==='GET'){
      const object=await storageRead(env.BUCKET,'rtc/pending');if(!object)return json({});
      const pending=await object.json();return json(Date.now()-pending.createdAt<60000?pending:{});
    }
    if(path.startsWith('/api/answer/')){
      const id=path.slice('/api/answer/'.length);if(!idPattern.test(id))return json({error:'Invalid session'},400);
      if(request.method==='POST'){
        if(Number(request.headers.get('content-length'))>100000)return json({error:'Answer is too large.'},413);
        let answer;try{answer=await request.json()}catch{return json({error:'Invalid answer'},400)}
        if(answer.type!=='answer'||typeof answer.sdp!=='string'||answer.sdp.length>100000)return json({error:'Invalid answer'},400);
        await env.BUCKET.put('rtc/answer/'+id,JSON.stringify({...answer,createdAt:Date.now()}),{httpMetadata:{contentType:'application/json'}});return json({ok:true});
      }
      if(request.method==='GET'){
        const object=await storageRead(env.BUCKET,'rtc/answer/'+id);if(!object)return new Response(null,{status:204,headers});
        const answer=await object.json();if(Date.now()-answer.createdAt>60000)return new Response(null,{status:204,headers});
        return json({sdp:answer.sdp,type:answer.type});
      }
      if(request.method==='DELETE'){await env.BUCKET.delete('rtc/answer/'+id);return json({ok:true})}
    }
    return json({error:'Unknown operation'},404);
  }
};
