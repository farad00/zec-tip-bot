import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createServer,request} from 'node:http';
import {once} from 'node:events';
import type {AddressInfo} from 'node:net';
import {State} from '../src/state.js';
import {TipStore} from '../src/tip-store.js';
import {loadConfig} from '../src/config.js';
import {accountRoutes} from '../src/account.js';
import {beginUserSignin,finishUserSignin,USER_SIGNIN_SCOPES} from '../src/user-auth.js';

const config=()=>loadConfig({PRIVATE_TIPPING_ENABLED:'true',ZCASH_NETWORK:'testnet',PUBLIC_BASE_URL:'https://tips.example.org',
  X_API_KEY:'BOT-KEY',X_API_SECRET:'BOT-SECRET',X_ACCESS_TOKEN:'BOT-TOKEN',X_ACCESS_SECRET:'BOT-TOKEN-SECRET',X_BOT_USER_ID:'100',
  X_USER_CLIENT_ID:'signup-client',X_USER_CLIENT_SECRET:'signup-secret'});
const grant=()=>({access_token:'MEMORY-ONLY-TOKEN',token_type:'bearer',expires_in:7200,scope:'tweet.read users.read'});
type Call={url:string;options:RequestInit};
function mocked(overrides:{token?:unknown;me?:unknown;tokenStatus?:number;meStatus?:number;revokeFails?:boolean}={}) {
  const calls:Call[]=[];
  const fetcher=(async(url,options)=>{
    const target=String(url);calls.push({url:target,options:options??{}});
    if(target.endsWith('/token'))return Response.json(overrides.token??grant(),{status:overrides.tokenStatus??200});
    if(target.endsWith('/me'))return Response.json(overrides.me??{data:{id:'200',username:'alice',name:'IGNORED'}},{status:overrides.meStatus??200});
    if(target.endsWith('/revoke')){if(overrides.revokeFails)throw new Error('SECRET-UPSTREAM');return Response.json({revoked:true});}
    throw new Error('Unexpected X endpoint');
  }) as typeof fetch;
  return {calls,fetcher};
}
async function site(run:(read:(path:string,form?:string,cookie?:string)=>Promise<Response>,state:State)=>Promise<void>,fetcher:typeof fetch,c=config()) {
  const state=new State(':memory:'),handler=accountRoutes(c,state,fetcher),server=createServer((req,res)=>{void handler(req,res);});
  server.listen(0,'127.0.0.1');await once(server,'listening');
  const base='http://127.0.0.1:'+(server.address() as AddressInfo).port;
  const read=(path:string,form?:string,cookie?:string)=>new Promise<Response>((resolve,reject)=>{
    const req=request(base+path,{method:form===undefined?'GET':'POST',headers:{Host:'tips.example.org',Origin:'https://tips.example.org',...(form===undefined?{}:{'Content-Type':'application/x-www-form-urlencoded'}),...(cookie?{Cookie:cookie}:{})}},res=>{
      const chunks:Buffer[]=[];res.on('data',chunk=>chunks.push(Buffer.from(chunk)));res.on('end',()=>{
        const headers=new Headers();for(const [key,value] of Object.entries(res.headers))if(value!==undefined)headers.set(key,Array.isArray(value)?value.join(', '):value);
        resolve(new Response(Buffer.concat(chunks),{status:res.statusCode!,headers}));
      });res.on('error',reject);
    });req.on('error',reject);req.end(form);
  });
  try {await run(read,state);}
  finally{server.closeAllConnections();await new Promise<void>((resolve,reject)=>server.close(error=>error?reject(error):resolve()));state.close();}
}
async function start(read:(path:string,form?:string,cookie?:string)=>Promise<Response>) {
  const page=await read('/account'),html=await page.text(),csrf=/name="csrf" value="([a-f0-9]+)"/.exec(html)![1]!;
  const cookie='__Host-zec_login='+csrf,response=await read('/account/login','csrf='+csrf,cookie);
  return {response,cookie,url:new URL(response.headers.get('location')??'https://invalid.test')};
}

test('user authorization requests exactly the documented read scopes and fresh S256 PKCE without bot credentials',()=>{
  const c=config(),a=beginUserSignin(c),b=beginUserSignin(c),url=new URL(a.url);
  assert.equal(url.origin+url.pathname,'https://x.com/i/oauth2/authorize');
  assert.deepEqual(url.searchParams.get('scope')!.split(' '),[...USER_SIGNIN_SCOPES]);
  assert.equal(url.searchParams.get('code_challenge_method'),'S256');
  assert.equal(url.searchParams.get('code_challenge'),createHash('sha256').update(a.verifier).digest('base64url'));
  assert.notEqual(a.state,b.state);assert.notEqual(a.verifier,b.verifier);assert.match(a.verifier,/^[\w-]{64}$/);
  assert.doesNotMatch(a.url,/BOT-|signup-secret|offline.access|write|email|follows|dm\./);
  assert.equal(url.searchParams.get('redirect_uri'),'https://tips.example.org/account/callback');
});

test('user callback preserves existing wallet identity/settings, revokes short-lived token and consumes its code once',async()=>{
  const {calls,fetcher}=mocked();
  await site(async(read,state)=>{
    const store=new TipStore(state);store.login('200','old_handle','testnet');
    store.settings('200','testnet',{amount:'0.001',daily:'0.01',fee:'0.001',enabled:false,consent:true,publicAck:false},'0.01');
    const before=store.profile('200','testnet')!,attempt=await start(read),callback='/account/callback?'+new URLSearchParams({state:attempt.url.searchParams.get('state')!,code:'valid-code'});
    assert.equal(calls.length,0);
    const result=await read(callback,undefined,attempt.cookie);assert.equal(result.status,303);assert.equal(result.headers.get('location'),'/account');
    const after=store.profile('200','testnet')!;assert.equal(after.username,'alice');assert.equal(after.default_zat,before.default_zat);assert.equal(after.enabled,0);
    assert.deepEqual(calls.map(call=>call.url),['https://api.x.com/2/oauth2/token','https://api.x.com/2/users/me','https://api.x.com/2/oauth2/revoke']);
    const body=calls[0]!.options.body as URLSearchParams;assert.equal(createHash('sha256').update(body.get('code_verifier')!).digest('base64url'),attempt.url.searchParams.get('code_challenge'));
    assert.equal(body.get('code'),'valid-code');assert.equal(body.get('redirect_uri'),'https://tips.example.org/account/callback');
    const saved=JSON.stringify(state.db.prepare('SELECT * FROM tip_sessions').all())+JSON.stringify(after);
    assert.doesNotMatch(saved,/MEMORY-ONLY-TOKEN|signup-secret|valid-code/);
    assert.equal((await read(callback,undefined,attempt.cookie)).status,403);assert.equal(calls.length,3);
    assert.ok(calls.every(call=>call.options.redirect==='error'&&!!call.options.signal));
    assert.doesNotMatch(JSON.stringify(calls),/BOT-KEY|BOT-SECRET|BOT-TOKEN/);
  },fetcher);
});

test('missing signup configuration fails closed without falling back to OAuth 1.0a',async()=>{
  const {calls,fetcher}=mocked();await site(async(read)=>{const a=await start(read);assert.equal(a.response.status,503);assert.equal(calls.length,0);},fetcher,{...config(),userClientId:'',userClientSecret:''});
});

test('state, browser cookie, duplicate query values and legacy OAuth callbacks cannot authenticate',async()=>{
  const {calls,fetcher}=mocked();await site(async(read)=>{
    const a=await start(read),s=a.url.searchParams.get('state')!;
    for(const path of ['/account/callback?code=c&state=wrong','/account/callback?code=c&state='+s+'&state='+s,
      '/account/callback?code=c&code=d&state='+s,'/account/callback?oauth_token=old&oauth_verifier=old','/account/callback?code=c&state='+s+'&oauth_token=old']){
      const response=await read(path,undefined,a.cookie);assert.equal(response.status,403);assert.equal(response.headers.get('referrer-policy'),'no-referrer');
    }
    assert.equal((await read('/account/callback?code=c&state='+s,undefined,'__Host-zec_login=wrong')).status,403);
    assert.equal(calls.length,0);
    assert.equal((await read('/account/callback?code=c&state='+s,undefined,a.cookie)).status,303);
  },fetcher);
});

test('authorization denial consumes the attempt without exposing provider text',async()=>{
  const {calls,fetcher}=mocked();await site(async(read)=>{
    const a=await start(read),s=a.url.searchParams.get('state')!,response=await read('/account/callback?state='+s+'&error=access_denied&error_description=SECRET-UPSTREAM',undefined,a.cookie);
    assert.equal(response.status,403);assert.doesNotMatch(await response.text(),/SECRET-UPSTREAM/);
    assert.equal((await read('/account/callback?state='+s+'&code=c',undefined,a.cookie)).status,403);assert.equal(calls.length,0);
  },fetcher);
});

test('expired attempts cannot exchange a code',async(t)=>{
  const {calls,fetcher}=mocked();await site(async(read)=>{
    const a=await start(read),now=Date.now();t.mock.method(Date,'now',()=>now+601000);
    try{assert.equal((await read('/account/callback?state='+a.url.searchParams.get('state')+'&code=c',undefined,a.cookie)).status,403);assert.equal(calls.length,0);}
    finally{t.mock.restoreAll();}
  },fetcher);
});

test('broader or incomplete token grants are rejected before identity lookup and revoked',async()=>{
  for(const change of [{scope:'users.read tweet.read tweet.write'},{scope:'users.read'},{scope:'users.read users.read'},
    {scope:undefined},{refresh_token:'UNEXPECTED'},{token_type:'mac'},{expires_in:0},{expires_in:7201}]){
    const {calls,fetcher}=mocked({token:{...grant(),...change}});let reads=0;
    await assert.rejects(finishUserSignin(config(),'c','v',()=>reads++,fetcher));assert.equal(reads,0);
    assert.deepEqual(calls.map(c=>c.url.split('/').pop()),['token','revoke']);
  }
});

test('identity failure still revokes the user token; revocation failure does not persist or expose it',async()=>{
  for(const me of [{data:{id:'200',username:'invalid handle'}},{data:{id:200,username:'alice'}},{data:{id:'200'}},{}]){
    const {calls,fetcher}=mocked({me});await assert.rejects(finishUserSignin(config(),'c','v',()=>{},fetcher));assert.equal(calls.at(-1)!.url,'https://api.x.com/2/oauth2/revoke');
  }
  const {fetcher}=mocked({revokeFails:true});assert.deepEqual(await finishUserSignin(config(),'c','v',()=>{},fetcher),{id:'200',username:'alice'});
});

test('failed token exchanges cannot leak upstream secrets or be replayed',async()=>{
  const {calls,fetcher}=mocked({tokenStatus:403,token:{error_description:'MEMORY-ONLY-TOKEN signup-secret'}});
  await site(async(read,state)=>{
    const a=await start(read),callback='/account/callback?state='+a.url.searchParams.get('state')+'&code=c',response=await read(callback,undefined,a.cookie);
    assert.equal(response.status,400);const html=await response.text();assert.match(html,/HTTP 403/);assert.doesNotMatch(html,/MEMORY-ONLY|signup-secret/);
    assert.equal((await read(callback,undefined,a.cookie)).status,403);assert.equal(calls.length,1);
    assert.equal(state.db.prepare('SELECT COUNT(*) AS n FROM tip_sessions').get()!.n,0);
  },fetcher);
});
