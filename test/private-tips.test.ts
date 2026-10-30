import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Readable} from 'node:stream';
import {bech32,createBase58check} from '@scure/base';
import {sha256} from '@noble/hashes/sha2.js';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {State} from '../src/state.js';
import {loadConfig} from '../src/config.js';
import {TipStore} from '../src/tip-store.js';
import {TipEngine,ProvenSpendRejection,type TipWalletBackend,type Proposal,type SignedSpend} from '../src/tip-engine.js';
import {accountRoutes} from '../src/account.js';
import {paymentServer} from '../src/web.js';
import {planMention,runBot,currentSnowflake} from '../src/bot.js';
import {XApi,XError,type Mention} from '../src/x.js';
import type {AccountWalletBackend} from '../src/wallet-accounts.js';
import {WALLET_ACCOUNT_SOCKET} from '../src/wallet-account-client.js';
const cfg=()=>loadConfig({PRIVATE_TIPPING_ENABLED:'true',ZCASH_NETWORK:'testnet',PUBLIC_BASE_URL:'https://tips.example.org',X_USER_CLIENT_ID:'dummy-client',X_USER_CLIENT_SECRET:'dummy-client-secret',X_API_KEY:'dummy-key',X_API_SECRET:'dummy-secret',X_ACCESS_TOKEN:'dummy-token',X_ACCESS_SECRET:'dummy-access-secret',X_BOT_USER_ID:'100',X_POSTING_ENABLED:'true'});
const address=(n:number)=>bech32.encode('ztestsapling',bech32.toWords(Buffer.alloc(43,n)),200);
const settings={amount:'0.001',daily:'0.0022',fee:'0.0001',enabled:true,publicAck:false,consent:true};

test('explicit wallet signup is session-owned, replay-safe and keeps funding and transfers disabled',async()=>{
 const c={...cfg(),walletAccounts:true,walletBrokerSocket:WALLET_ACCOUNT_SOCKET};
 const state=new State(':memory:'),store=new TipStore(state),native:{account:string;name:string}[]=[],calls:string[]=[];
 const uuid='11111111-1111-4111-8111-111111111111';
 const wallet:AccountWalletBackend={health:async()=>({network:'testnet',synced:false,currentConsensusVerified:true,backupConfirmed:true,accountCreationReady:true,depositsEnabled:false,transfersEnabled:false}),
  accounts:async()=>native,createAccount:async name=>{calls.push(name);native.push({account:uuid,name});return {account:uuid};},
  shieldedAddress:async(account,index)=>{assert.equal(index,0);return {network:'testnet',account,address:address(1)};}};
 const handler=accountRoutes(c,state,fetch,async()=>undefined,wallet);
 try{
  const alice=store.login('200','alice','testnet');store.login('300','bob','testnet');
  const cookie='__Host-zec_account='+alice.token,form='csrf='+alice.csrf+'&wallet_consent=yes';
  assert.equal((await route(handler,'/account/wallet','POST',form,cookie,'https://evil.example')).status,403);
  assert.equal((await route(handler,'/account/wallet','POST',form+'&user_id=300',cookie)).status,400);
  assert.equal(calls.length,0);
  assert.equal((await route(handler,'/account/wallet','POST',form,cookie)).status,303);
  assert.equal((await route(handler,'/account/wallet','POST',form,cookie)).status,303);
  assert.equal(calls.length,1);assert.equal(store.wallet('200','testnet')!.account,uuid);assert.equal(store.wallet('300','testnet'),undefined);
  assert.equal(store.profile('200','testnet')!.enabled,0);
  const page=await route(handler,'/account','GET','',cookie);
  assert.match(page.body,/Created and linked/);assert.ok(!page.body.includes(address(1)));assert.match(page.body,/name="enabled" value="yes" disabled/);
  const enabled='csrf='+alice.csrf+'&amount=0.001&daily=0.1&fee=0.0001&consent=yes&enabled=yes';
  assert.equal((await route(handler,'/account/settings','POST',enabled,cookie)).status,400);assert.equal(store.profile('200','testnet')!.enabled,0);
 }finally{state.close();}
});

test('wallet creation requires fresh authentication and explicit consent before native mutation',async()=>{
 const state=new State(':memory:'),store=new TipStore(state);let mutations=0;
 const wallet:AccountWalletBackend={health:async()=>({network:'testnet',synced:true,currentConsensusVerified:true,backupConfirmed:true,accountCreationReady:true,depositsEnabled:false,transfersEnabled:false}),
  accounts:async()=>[],createAccount:async()=>{mutations++;throw new Error('unexpected');},shieldedAddress:async()=>{throw new Error('unexpected');}};
 const handler=accountRoutes({...cfg(),walletAccounts:true,walletBrokerSocket:WALLET_ACCOUNT_SOCKET},state,fetch,async()=>undefined,wallet);
 try{
  const old=store.login('200','alice','testnet',Date.now()-700_000);
  assert.equal((await route(handler,'/account/wallet','POST','csrf='+old.csrf+'&wallet_consent=yes','__Host-zec_account='+old.token)).status,403);
  const fresh=store.login('200','alice','testnet');
  assert.equal((await route(handler,'/account/wallet','POST','csrf='+fresh.csrf,'__Host-zec_account='+fresh.token)).status,400);
  assert.equal(mutations,0);
 }finally{state.close();}
});
function ready(s:TipStore,id:string,n:number,now=Date.now()-3000) {s.login(id,'user'+id,'testnet',now);s.attachVerifiedTestnetWallet(id,'account-'+id,address(n));s.settings(id,'testnet',settings,'1',now);}
const mention=(text:string,id=currentSnowflake(),author='200'):Mention=>({id,text,author_id:author,entities:{mentions:[{username:'zectipbot',id:'100'}]}});
function queued(s:TipStore,source='123',now=Date.now()) {return s.enqueue(source,'200','300','testnet',now,now);}
function backend(store:TipStore) {
  const calls:string[]=[];let failBroadcast=true, confirms=0;
  const wallet:TipWalletBackend={
    health:async()=>({network:'testnet',synced:true}),
    prepare:async(input)=>{calls.push('prepare');return {network:'testnet',account:input.account,recipient:input.recipient,amountZat:input.amountZat,feeZat:10000,privacy:'FullPrivacy',transparentInputs:0,transparentOutputs:0,proposal:'test-only-unsigned-proposal'};},
    sign:async()=>{calls.push('sign');return {txid:'a'.repeat(64),raw:'abcd'};},
    broadcast:async(tx)=>{calls.push('broadcast:'+tx.raw);if(failBroadcast)throw new Error('lost response');return {txid:tx.txid};},
    confirmations:async()=>confirms
  };
  return {wallet,calls,engine:new TipEngine(store,wallet),allowBroadcast:()=>{failBroadcast=false;},confirm:(n:number)=>{confirms=n;}};
}
function request(path:string,method='GET',body='',cookies='',origin='https://tips.example.org') {
  const req=Readable.from(body?[Buffer.from(body)]:[]) as IncomingMessage;
  req.url=path;req.method=method;req.headers={host:'tips.example.org',origin,cookie:cookies,'content-type':'application/x-www-form-urlencoded'};
  return req;
}
function response() {
  const headers:Record<string,string|string[]|number>={};let status=200,body='';let done!:()=>void;
  const finished=new Promise<void>(resolve=>{done=resolve;});
  const res={setHeader:(key:string,value:string|string[]|number)=>{headers[key.toLowerCase()]=value;},writeHead:(code:number,extra?:Record<string,string>)=>{status=code;for(const [k,v] of Object.entries(extra??{}))headers[k.toLowerCase()]=v;return res;},end:(chunk?:string|Buffer)=>{body+=chunk?.toString()??'';done();return res;}};
  return {res:res as unknown as ServerResponse,headers,finished,get status(){return status;},get body(){return body;}};
}
async function route(handler:ReturnType<typeof accountRoutes>,path:string,method='GET',body='',cookies='',origin='https://tips.example.org') {const result=response();assert.equal(await handler(request(path,method,body,cookies,origin),result.res),true);await result.finished;return result;}

test('private mode cannot enable Mainnet; presets require explicit consent and a real wallet mapping to enable',()=>{
  assert.throws(()=>loadConfig({PRIVATE_TIPPING_ENABLED:'true'}),/testnet/);
  const state=new State(':memory:'),s=new TipStore(state);
  try {s.login('200','alice','testnet');s.settings('200','testnet',{...settings,enabled:false},'1');assert.equal(s.profile('200','testnet')!.default_zat,100000);assert.equal(s.profile('200','testnet')!.enabled,0);
    assert.throws(()=>s.settings('200','testnet',settings,'1'),/verified/);
    for(const values of [{...settings,enabled:false,consent:false},{...settings,enabled:false,amount:'1e-3'},{...settings,enabled:false,daily:'0.001'},{...settings,enabled:false,fee:'-1'}])assert.throws(()=>s.settings('200','testnet',values,'1'));
    assert.equal(s.profile('200','testnet')!.default_zat,100000);
  }finally{state.close();}
});

test('sync observation is only read for an authenticated account and cannot enable its wallet',async()=>{
 const c={...cfg(),walletHealthFile:'/run/zec-wallet-health/status.json'};
 const state=new State(':memory:'),store=new TipStore(state);let reads=0;
 const handler=accountRoutes(c,state,fetch,async()=>{reads++;return {version:1,observedAt:Date.now(),network:'testnet',nodeRpc:true,walletRpc:true,nodeHeight:659600,nodeProgress:0.183,walletStatus:'timeout'};});
 try {
  await route(handler,'/account');assert.equal(reads,0);
  const session=store.login('200','alice','testnet');const result=await route(handler,'/account','GET','','__Host-zec_account='+session.token);
  assert.equal(reads,1);assert.match(result.body,/Wallet API connected/);assert.match(result.body,/18\.3%/);
  assert.match(result.body,/Your tipping wallet: not provisioned/);
  assert.match(result.body,/name="enabled" value="yes" disabled/);assert.equal(store.wallet('200','testnet'),undefined);assert.equal(store.profile('200','testnet')!.enabled,0);
 }finally{state.close();}
});
test('verified ID survives username change; sessions are hashed, expire and cannot cross networks',()=>{
  const state=new State(':memory:'),s=new TipStore(state);
  try {const session=s.login('200','alice','testnet',1000);s.login('200','renamed','testnet',2000);assert.equal(s.profile('200','testnet')!.username,'renamed');
    assert.equal(s.session(session.token,'testnet',2000)!.user_id,'200');assert.equal(s.session(session.token,'mainnet',2000),undefined);assert.equal(s.session(session.token,'testnet',3_601_000),undefined);
    assert.ok(!JSON.stringify(state.db.prepare('SELECT * FROM tip_sessions').all()).includes(session.token));s.logout(session.token);assert.equal(s.session(session.token,'testnet',2000),undefined);
  }finally{state.close();}
});
test('wallet mapping rejects transparent/wrong-network addresses, cross-user reuse and reassignment',()=>{
  const state=new State(':memory:'),s=new TipStore(state);
  try {s.login('200','alice','testnet');s.login('300','bob','testnet');const transparent=createBase58check(sha256).encode(Buffer.concat([Buffer.from('1d25','hex'),Buffer.alloc(20,1)]));assert.throws(()=>s.attachVerifiedTestnetWallet('200','a',transparent),/shielded/);
    assert.throws(()=>s.attachVerifiedTestnetWallet('200','a',bech32.encode('zs',bech32.toWords(Buffer.alloc(43,1)),200)));
    s.attachVerifiedTestnetWallet('200','a',address(1));assert.throws(()=>s.attachVerifiedTestnetWallet('300','a',address(2)));assert.throws(()=>s.attachVerifiedTestnetWallet('300','b',address(1)));assert.throws(()=>s.attachVerifiedTestnetWallet('200','b',address(2)));
  }finally{state.close();}
});
test('exact default is snapshotted; duplicate commands and atomic fee-inclusive daily reservations cannot overspend cap',()=>{
  const state=new State(':memory:'),s=new TipStore(state);
  try {ready(s,'200',1);ready(s,'300',2);assert.equal(queued(s,'1').amount_zat,100000);assert.equal(queued(s,'1').reserved_zat,110000);assert.equal(queued(s,'2').status,'queued');assert.equal(queued(s,'3').reason,'daily_limit');
    s.settings('200','testnet',{...settings,amount:'0.0005'},'1');assert.equal(s.job('1')!.amount_zat,100000);assert.equal(queued(s,'1').amount_zat,100000);
  }finally{state.close();}
});
test('stale, self, opted-out, unconfigured and unregistered commands never reserve spending',()=>{
  const state=new State(':memory:'),s=new TipStore(state);const now=Date.now();
  try {ready(s,'200',1,now-1000);ready(s,'300',2,now-1000);assert.equal(s.enqueue('1','200','300','testnet',now-2000,now).reason,'stale_command');assert.equal(s.enqueue('2','200','200','testnet',now,now).reason,'self_tip');assert.equal(s.enqueue('3','200','400','testnet',now,now).reason,'wallet_unavailable');state.opt('200',true);assert.equal(queued(s,'4').reason,'tips_paused');assert.equal(s.enqueue('5','500','300','testnet',now,now).reason,'setup_required');
    assert.ok(s.history('200','testnet').every(j=>j.reserved_zat===undefined));assert.equal(s.job('4')!.reserved_zat,0);assert.equal(s.history('300','testnet').length,0);
  }finally{state.close();}
});
test('reply tip resolves immediate parent author; explicit tip resolves fresh X ID; preview changes no job or cursor',async()=>{
  const state=new State(':memory:'),s=new TipStore(state),c=cfg();
  try {ready(s,'200',1);ready(s,'300',2);const m={...mention('@zectipbot tip'),referenced_tweets:[{type:'quoted',id:'50'},{type:'replied_to',id:'99'}]};let looked='';
    const plan=await planMention(m,c,state,{lookup:async()=>{throw new Error('wrong resolver');},postAuthor:async(id)=>{looked=id;return {id:'300',username:'bob'};}});assert.equal(looked,'99');assert.equal(s.job(m.id),undefined);assert.equal(plan?.text,undefined);plan!.apply!();assert.equal(s.job(m.id)!.recipient,'300');
    const explicit=mention('@zectipbot tip @renamed',String(BigInt(m.id)+1n));const p=await planMention(explicit,c,state,{lookup:async(name)=>{assert.equal(name,'renamed');return {id:'300',username:name};}});p!.apply!();assert.equal(s.job(explicit.id)!.recipient,'300');
    const preview=mention('@zectipbot tip @bob',String(BigInt(m.id)+2n));state.set('since_id','1');const api=new XApi(c,()=>{},(async(url)=>Response.json(String(url).includes('/mentions?')?{data:[preview]}:String(url).includes('/by/username/')?{data:{id:'300',username:'bob'}}:{data:{id:'100',username:'zectipbot'}})) as typeof fetch);
    await runBot(c,state,api,false);assert.equal(s.job(preview.id),undefined);assert.equal(state.get('since_id'),'1');
  }finally{state.close();}
});
test('private command errors never echo amount, address, recipient registration or fabricate payment success',async()=>{
  const state=new State(':memory:'),c=cfg(),api={lookup:async()=>({id:'300',username:'bob'})};
  try {for(const text of ['@zectipbot tip @bob 0.12345678 ZEC','@zectipbot register SECRET_ADDRESS public','@zectipbot request @bob 0.12345678 ZEC']){const plan=await planMention(mention(text),c,state,api);assert.ok(plan?.text);assert.doesNotMatch(plan.text,/0\.12345678|SECRET_ADDRESS|Payment sent/);assert.equal(plan.payment,undefined);}
    const noParent=await planMention(mention('@zectipbot tip'),c,state,api);assert.match(noParent?.text??'',/Reply directly/);
  }finally{state.close();}
});
test('stopping replies disables spending without deleting hosted wallet access; start does not re-enable spending',async()=>{
  const state=new State(':memory:'),s=new TipStore(state),c=cfg(),api={lookup:async()=>({id:'300',username:'bob'})};
  try {ready(s,'200',1);(await planMention(mention('@zectipbot stop'),c,state,api))!.apply!();assert.equal(s.profile('200','testnet')!.enabled,0);assert.equal(s.wallet('200','testnet')!.account,'account-200');(await planMention(mention('@zectipbot start'),c,state,api))!.apply!();assert.equal(s.profile('200','testnet')!.enabled,0);
  }finally{state.close();}
});
test('a broadcast timeout resumes identical signed bytes without a second preparation/signature; confirmation and reorg are distinguished',async()=>{
  const state=new State(':memory:'),s=new TipStore(state);
  try {ready(s,'200',1);ready(s,'300',2);queued(s);const b=backend(s);await b.engine.process('123');assert.equal(s.job('123')!.status,'broadcasting');b.allowBroadcast();const restarted=new TipEngine(s,b.wallet);await restarted.process('123');assert.equal(s.job('123')!.status,'submitted');assert.deepEqual(b.calls,['prepare','sign','broadcast:abcd','broadcast:abcd']);b.confirm(3);await restarted.process('123');assert.equal(s.job('123')!.status,'confirmed');b.confirm(0);await restarted.process('123');assert.equal(s.job('123')!.status,'submitted');assert.equal(s.job('123')!.reason,'chain_reorganization');
  }finally{state.close();}
});
test('fee excess, privacy downgrade and altered recipient/amount fail before signing',async()=>{
  for(const change of [{feeZat:10001},{privacy:'NoPrivacy'},{transparentInputs:1},{transparentOutputs:1},{recipient:address(3)},{amountZat:1}]) {
    const state=new State(':memory:'),s=new TipStore(state);
    try {ready(s,'200',1);ready(s,'300',2);queued(s);const b=backend(s),prepare=b.wallet.prepare;b.wallet.prepare=async(input)=>({...await prepare(input),...change}) as Proposal;await b.engine.process('123');assert.equal(s.job('123')!.status,'review');assert.deepEqual(b.calls,['prepare']);}
    finally{state.close();}
  }
});
test('uncertain signing survives restart without blind retries; proven insufficient funds fails without broadcast',async()=>{
  const state=new State(':memory:'),s=new TipStore(state);
  try {ready(s,'200',1);ready(s,'300',2);queued(s);const b=backend(s);b.wallet.sign=async()=>{b.calls.push('sign');throw new Error('lost response');};await b.engine.process('123');await new TipEngine(s,b.wallet).process('123');assert.equal(s.job('123')!.status,'review');assert.deepEqual(b.calls,['prepare','sign']);queued(s,'124');b.wallet.prepare=async()=>{throw new ProvenSpendRejection('insufficient');};await b.engine.process('124');assert.equal(s.job('124')!.status,'failed');assert.equal(s.job('124')!.reason,'wallet_rejected');}
  finally{state.close();}
});
test('unsynced/wrong-network wallet and paused user cannot authorize signing',async()=>{
  for(const bad of [{network:'mainnet',synced:true},{network:'testnet',synced:false}]){
    const state=new State(':memory:'),s=new TipStore(state);
    try {ready(s,'200',1);ready(s,'300',2);queued(s);const b=backend(s);b.wallet.health=async()=>bad as {network:'testnet';synced:boolean};await assert.rejects(b.engine.process('123'),/synchronized/);assert.deepEqual(b.calls,[]);s.disable('200','testnet');await b.engine.process('123');assert.equal(s.job('123')!.status,'failed');}
    finally{state.close();}
  }
});
test('public and unauthenticated routes disclose no existing payment or financial settings',async()=>{
  const state=new State(':memory:'),s=new TipStore(state),c=cfg();
  try {ready(s,'200',1);const p=state.createPayment({address:address(3),amount:'0.87654321',recipient:'secret_recipient',source:'old',network:'testnet',expires:Date.now()+60000});const handler=accountRoutes(c,state);
    for(const path of ['/','/account']){const result=await route(handler,path);assert.doesNotMatch(result.body,/0\.87654321|secret_recipient|ztestsapling1/);assert.equal(result.headers['cache-control'],'no-store');assert.equal(result.headers['referrer-policy'],'same-origin');}
    const server=paymentServer(c,state);for(const path of ['/p/'+p.id,'/p/'+p.id+'/card.png']){const result=response();server.emit('request',request(path),result.res);await result.finished;assert.equal(result.status,404);assert.doesNotMatch(result.body,/secret_recipient|ztestsapling1|0\.87654321/);}server.close();
  }finally{state.close();}
});
test('authenticated settings are owner-scoped, require origin and CSRF, and cannot enable unprovisioned funds',async()=>{
  const state=new State(':memory:'),s=new TipStore(state),handler=accountRoutes(cfg(),state);
  try {const alice=s.login('200','alice','testnet'),bob=s.login('300','bob','testnet');s.settings('200','testnet',{...settings,amount:'0.00678901',daily:'0.1',enabled:false},'1');
    const bobPage=await route(handler,'/account','GET','','__Host-zec_account='+bob.token);assert.doesNotMatch(bobPage.body,/0\.00678901/);assert.match(bobPage.body,/@bob/);
    const form=new URLSearchParams({csrf:alice.csrf,amount:'0.001',daily:'0.01',fee:'0.0001',consent:'yes'}).toString();
    assert.equal((await route(handler,'/account/settings','POST',form,'__Host-zec_account='+alice.token,'https://evil.example')).status,403);
    assert.equal((await route(handler,'/account/settings','POST',form.replace(alice.csrf,'bad'),'__Host-zec_account='+alice.token)).status,403);
    assert.equal((await route(handler,'/account/settings','POST',form,'__Host-zec_account='+alice.token)).status,303);assert.equal(s.profile('200','testnet')!.default_zat,100000);assert.equal(s.profile('300','testnet')!.default_zat,null);
    assert.equal((await route(handler,'/account/settings','POST',form+'&enabled=yes','__Host-zec_account='+alice.token)).status,400);assert.equal(s.profile('200','testnet')!.enabled,0);
    assert.equal((await route(handler,'/account/logout','POST','csrf='+alice.csrf,'__Host-zec_account='+alice.token)).status,303);assert.equal(s.session(alice.token,'testnet'),undefined);
  }finally{state.close();}
});
test('X signup verifies arbitrary user independently of pinned bot; tokens are not persisted; callback replay and cookie theft are rejected',async()=>{
  const state=new State(':memory:'),s=new TipStore(state);let requests=0;
  const fetcher=(async(url)=>{requests++;if(String(url).endsWith('/token'))return Response.json({access_token:'private-user-access',token_type:'bearer',scope:'users.read tweet.read',expires_in:7200});if(String(url).endsWith('/revoke'))return Response.json({revoked:true});return Response.json({data:{id:'200',username:'alice'}});}) as typeof fetch;
  const handler=accountRoutes(cfg(),state,fetcher);
  try {const landing=await route(handler,'/account'),nonce=/name="csrf" value="([a-f0-9]+)"/.exec(landing.body)![1]!;const cookies='__Host-zec_login='+nonce;
    const started=await route(handler,'/account/login','POST','csrf='+nonce,cookies);assert.equal(started.status,303);
    const callback='/account/callback?code=verified&state='+new URL(String(started.headers.location)).searchParams.get('state');assert.equal((await route(handler,callback,'GET','','__Host-zec_login=other')).status,403);assert.equal(requests,0);
    const completed=await route(handler,callback,'GET','',cookies);assert.equal(completed.status,303);assert.match(String(completed.headers['set-cookie']),/Secure/);assert.match(String(completed.headers['set-cookie']),/HttpOnly/);assert.equal(s.profile('200','testnet')!.username,'alice');
    const data=JSON.stringify(state.db.prepare('SELECT * FROM tip_sessions').all());assert.doesNotMatch(data,/private-user-access|private-user-secret|request-secret/);
    assert.equal((await route(handler,callback,'GET','',cookies)).status,403);assert.equal(requests,3);
  }finally{state.close();}
});


test('missing recipient is a private failed command rather than blocking all later mentions',async()=>{
  const state=new State(':memory:'),s=new TipStore(state);
  try {ready(s,'200',1);const m=mention('@zectipbot tip @missing');const p=await planMention(m,cfg(),state,{lookup:async()=>{throw new XError(404);}});assert.equal(p?.text,undefined);p!.apply!();assert.equal(s.job(m.id)!.status,'failed');assert.equal(s.job(m.id)!.reserved_zat,0);}
  finally{state.close();}
});
test('concurrent processing within one engine cannot prepare or sign a command twice',async()=>{
  const state=new State(':memory:'),s=new TipStore(state);
  try {ready(s,'200',1);ready(s,'300',2);queued(s);const b=backend(s);await Promise.all([b.engine.process('123'),b.engine.process('123')]);assert.deepEqual(b.calls,['prepare','sign','broadcast:abcd']);}
  finally{state.close();}
});
test('interrupted preparing or signing state is held, not automatically repeated',async()=>{
  for(const stage of ['preparing','signing']) {const state=new State(':memory:'),s=new TipStore(state);
    try {ready(s,'200',1);ready(s,'300',2);queued(s);state.db.prepare('UPDATE tip_jobs SET status=? WHERE source=?').run(stage,'123');const b=backend(s);await b.engine.process('123');assert.equal(s.job('123')!.status,'review');assert.deepEqual(b.calls,[]);}
    finally{state.close();}
  }
});


test('sign-in pages preserve same-origin browser form requests while OAuth callback URLs suppress referrers',async()=>{
  const state=new State(':memory:');let requests=0;
  const handler=accountRoutes(cfg(),state,(async()=>{requests++;return new Response('oauth_token=request-token&oauth_token_secret=request-secret&oauth_callback_confirmed=true');}) as typeof fetch);
  try {
    const landing=await route(handler,'/account');assert.equal(landing.headers['referrer-policy'],'same-origin');
    const nonce=/name="csrf" value="([a-f0-9]+)"/.exec(landing.body)![1]!, cookies='__Host-zec_login='+nonce;
    for(const origin of ['null','https://evil.example']) {const blocked=await route(handler,'/account/login','POST','csrf='+nonce,cookies,origin);assert.equal(blocked.status,403);assert.match(blocked.body,/href="\/account"/);}
    const missing=request('/account/login','POST','csrf='+nonce,cookies);delete missing.headers.origin;missing.headers.referer='https://tips.example.org/account';const denied=response();await handler(missing,denied.res);await denied.finished;assert.equal(denied.status,403);assert.equal(requests,0);
    const invalidCookie=await route(handler,'/account/login','POST','csrf='+nonce,'__Host-zec_login=bad');assert.equal(invalidCookie.status,403);assert.equal(requests,0);
    const started=await route(handler,'/account/login','POST','csrf='+nonce,cookies);assert.equal(started.status,303);assert.match(String(started.headers.location),/^https:\/\/x\.com\/i\/oauth2\/authorize\?/);assert.equal(requests,0);
    const callback=await route(handler,'/account/callback?oauth_token=unknown&oauth_verifier=invalid');assert.equal(callback.headers['referrer-policy'],'no-referrer');assert.equal(callback.status,403);
  }finally{state.close();}
});

test('account OAuth diagnostics expose HTTP status without echoing upstream messages or credentials',async()=>{
  const state=new State(':memory:');
  const handler=accountRoutes(cfg(),state,(async()=>Response.json({errors:[{code:415,message:'secret-token private-key arbitrary-upstream-text'}]},{status:403})) as typeof fetch);
  try {const landing=await route(handler,'/account'),nonce=/name="csrf" value="([a-f0-9]+)"/.exec(landing.body)![1]!;
    const cookies='__Host-zec_login='+nonce,started=await route(handler,'/account/login','POST','csrf='+nonce,cookies);
    const callback='/account/callback?code=verified&state='+new URL(String(started.headers.location)).searchParams.get('state');
    const error=await route(handler,callback,'GET','',cookies);assert.equal(error.status,400);assert.match(error.body,/HTTP 403/);assert.match(error.body,/callback URL/);assert.doesNotMatch(error.body,/secret-token|private-key|arbitrary-upstream-text|dummy-secret/);}
  finally{state.close();}
});
