import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Readable} from 'node:stream';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {State} from '../src/state.js';
import {TipStore} from '../src/tip-store.js';
import {TipEngine,type TipWalletBackend} from '../src/tip-engine.js';
import {WalletTransferClient,type TransferTransport} from '../src/wallet-transfer-client.js';
import {WALLET_ACCOUNT_SOCKET} from '../src/wallet-account-client.js';
import {processWalletBatch} from '../src/wallet-worker.js';
import {accountRoutes} from '../src/account.js';
import {loadConfig} from '../src/config.js';
import {testUnifiedAddress} from './wallet-test-address.js';
const A='11111111-1111-4111-8111-111111111111',address=testUnifiedAddress(1),dest=testUnifiedAddress(2);
const health={network:'testnet' as const,synced:true,currentConsensusVerified:true,backupConfirmed:true,accountCreationReady:true,depositsEnabled:false,transfersEnabled:true};
function setup(){const state=new State(':memory:'),store=new TipStore(state);store.login('200','alice','testnet');store.attachVerifiedTestnetWallet('200',A,address);return {state,store};}
function wallet():TipWalletBackend {return {health:async()=>({network:'testnet',synced:true}),
 prepare:async input=>({network:'testnet',account:input.account,recipient:input.recipient,amountZat:input.amountZat,feeZat:10000,privacy:'FullPrivacy',transparentInputs:0,transparentOutputs:0,proposal:'opaque-broker-token'}),
 sign:async()=>({txid:'a'.repeat(64),raw:'aabb'}),broadcast:async()=>({txid:'a'.repeat(64)}),confirmations:async()=>3};}

test('transfer adapter sends only an opaque hashed idempotency key and rejects changed payment terms',async()=>{
 const calls:{method:string;path:string;body?:Record<string,unknown>}[]=[];
 const transport:TransferTransport=async(method,path,body)=>{
  calls.push({method,path,body});
  if(path==='/health')return health;
  if(path==='/prepare')return {network:'testnet',account:A,recipient:dest,amountZat:1,feeZat:10000,privacy:'FullPrivacy',transparentInputs:0,transparentOutputs:0,proposal:'b'.repeat(48)};
  if(path==='/sign')return {txid:'a'.repeat(64),raw:'aabb'};
  if(path==='/broadcast')return {txid:'a'.repeat(64)};
  return {confirmations:3};
 };
 const client=new WalletTransferClient(WALLET_ACCOUNT_SOCKET,transport);
 await client.health();
 const p=await client.prepare({idempotencyKey:'2115486213627445250',account:A,recipient:dest,amountZat:1,maxFeeZat:10000});
 const signed=await client.sign({idempotencyKey:'2115486213627445250',proposal:p});await client.broadcast(signed);await client.confirmations(signed.txid);
 assert.match(calls[1]!.body!.idempotencyKey as string,/^[a-f0-9]{64}$/);
 assert.equal(calls[1]!.body!.idempotencyKey,calls[2]!.body!.idempotencyKey);
 assert.deepEqual(Object.keys(calls[2]!.body!).sort(),['idempotencyKey','proposal']);
 assert.ok(!JSON.stringify(calls).includes('2115486213627445250'));
 const bad=new WalletTransferClient(WALLET_ACCOUNT_SOCKET,async()=>({...p,amountZat:2}));
 await assert.rejects(bad.prepare({idempotencyKey:'1',account:A,recipient:dest,amountZat:1,maxFeeZat:10000}));
 for(const change of [{transfersEnabled:false},{network:'mainnet'},{backupConfirmed:false}])await assert.rejects(new WalletTransferClient(WALLET_ACCOUNT_SOCKET,async()=>({...health,...change})).health());
});

test('manual withdrawal is owner-bound, immutable, replay-safe and independent of automatic-tip opt-out',async()=>{
 const s=setup();try{
  s.store.login('300','bob','testnet');s.store.disable('200','testnet');s.state.opt('200',true);
  const form=s.store.withdrawalForm('200');
  assert.throws(()=>s.store.enqueueWithdrawal('300',form,dest,'0.001','0.0001'));
  const first=s.store.enqueueWithdrawal('200',form,dest,'0.001','0.0001');
  assert.equal(s.store.enqueueWithdrawal('200',form,dest,'0.001','0.0001').source,first.source);
  assert.throws(()=>s.store.enqueueWithdrawal('200',form,address,'0.001','0.0001'));
  assert.equal(s.store.history('300','testnet').length,0);
  const engine=new TipEngine(s.store,wallet());await engine.process(first.source);
  assert.equal(s.store.job(first.source)!.status,'confirmed');assert.equal(s.store.profile('200','testnet')!.enabled,0);
 }finally{s.state.close();}
});

test('wallet worker processes persisted queued withdrawals without X access and checks confirmations',async()=>{
 const s=setup();try{
  const job=s.store.enqueueWithdrawal('200',s.store.withdrawalForm('200'),dest,'0.001','0.0001');
  const w=wallet();let signs=0;w.sign=async()=>{signs++;return {txid:'a'.repeat(64),raw:'aabb'};};
  await processWalletBatch(s.state,w,new AbortController().signal);
  await processWalletBatch(s.state,w,new AbortController().signal);
  assert.equal(signs,1);assert.equal(s.store.job(job.source)!.status,'confirmed');
  w.confirmations=async()=>0;await processWalletBatch(s.state,w,new AbortController().signal);
  assert.equal(s.store.job(job.source)!.status,'submitted');
 }finally{s.state.close();}
});

async function route(handler:ReturnType<typeof accountRoutes>,url:string,method:string,body:string,cookie:string,origin='https://tips.example.org'){
 const request=Readable.from(body?[Buffer.from(body)]:[]) as IncomingMessage;
 request.url=url;request.method=method;request.headers={host:'tips.example.org',cookie,origin,'content-type':'application/x-www-form-urlencoded'};
 let status=200,text='';const headers:Record<string,unknown>={};
 const response={setHeader:(key:string,value:unknown)=>{headers[key]=value;},writeHead:(code:number)=>{status=code;return response;},end:(value?:string)=>{text=value??'';return response;}} as unknown as ServerResponse;
 await handler(request,response);return {status,text,headers};
}
test('withdrawal page requires fresh X sign-in, same-origin CSRF, review and explicit final confirmation',async()=>{
 const s=setup();try{
  const c=loadConfig({PRIVATE_TIPPING_ENABLED:'true',WALLET_ACCOUNTS_ENABLED:'true',HOSTED_WALLETS_ENABLED:'true',WALLET_BROKER_SOCKET:WALLET_ACCOUNT_SOCKET,ZCASH_NETWORK:'testnet',PUBLIC_BASE_URL:'https://tips.example.org'});
  const native={health:async()=>health,accounts:async()=>[],createAccount:async()=>({account:A}),shieldedAddress:async()=>({network:'testnet' as const,account:A,address})};
  const handler=accountRoutes(c,s.state,fetch,async()=>undefined,native);
  const session=s.store.login('200','alice','testnet'),cookie='__Host-zec_account='+session.token;
  const page=await route(handler,'/account','GET','',cookie);
  const form=page.text.match(/name="form_id" value="([a-f0-9]{32})"/)![1]!;
  const values=new URLSearchParams({csrf:session.csrf,form_id:form,address:dest,amount:'0.001',fee:'0.0001'});
  assert.equal((await route(handler,'/account/withdraw-review','POST',values.toString(),cookie,'https://evil.example')).status,403);
  const review=await route(handler,'/account/withdraw-review','POST',values.toString(),cookie);
  assert.equal(review.status,200);assert.ok(review.text.includes(dest));assert.match(review.text,/Confirm Testnet withdrawal/);
  assert.equal(s.store.history('200','testnet').length,0);
  assert.equal((await route(handler,'/account/withdraw','POST',values.toString(),cookie)).status,400);
  values.set('confirm','yes');assert.equal((await route(handler,'/account/withdraw','POST',values.toString(),cookie)).status,303);
  assert.equal((await route(handler,'/account/withdraw','POST',values.toString(),cookie)).status,303);
  assert.equal(s.store.history('200','testnet').length,1);
  const old=s.store.login('200','alice','testnet',Date.now()-700_000);values.set('csrf',old.csrf);
  assert.equal((await route(handler,'/account/withdraw','POST',values.toString(),'__Host-zec_account='+old.token)).status,403);
 }finally{s.state.close();}
});
