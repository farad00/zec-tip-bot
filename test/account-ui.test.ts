import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Readable} from 'node:stream';
import {Script} from 'node:vm';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {accountRoutes} from '../src/account.js';
import {PRIVATE_JS} from '../src/account-ui.js';
import {State} from '../src/state.js';
import {TipStore} from '../src/tip-store.js';
import {loadConfig} from '../src/config.js';
import type {AccountWalletBackend} from '../src/wallet-accounts.js';
import {testUnifiedAddress} from './wallet-test-address.js';

async function read(handler:ReturnType<typeof accountRoutes>,path:string,cookie=''){
 const req=Readable.from([]) as IncomingMessage;
 req.url=path;req.method='GET';req.headers={host:'tips.example.org',cookie};
 let body='',status=200;const headers:Record<string,unknown>={};
 const res={setHeader:(key:string,value:unknown)=>{headers[key]=value;},writeHead:(code:number)=>{status=code;return res;},end:(text?:string)=>{body=text??'';return res;}} as unknown as ServerResponse;
 await handler(req,res);return {body,status,headers};
}
test('deposit QR, balance and address stay within the authenticated owner page and deposit gate',async()=>{
 const c=loadConfig({PRIVATE_TIPPING_ENABLED:'true',ZCASH_NETWORK:'testnet',PUBLIC_BASE_URL:'https://tips.example.org',X_API_KEY:'dummy',X_API_SECRET:'dummy'});
 const state=new State(':memory:'),store=new TipStore(state),alice=store.login('200','alice','testnet'),bob=store.login('300','bob','testnet');
 const address=testUnifiedAddress(1),account='11111111-1111-4111-8111-111111111111';store.attachVerifiedTestnetWallet('200',account,address);
 let deposits=false,reads=0;
 const backend:AccountWalletBackend={health:async()=>({network:'testnet',synced:true,currentConsensusVerified:true,backupConfirmed:true,accountCreationReady:true,depositsEnabled:deposits,transfersEnabled:false}),accounts:async()=>[],createAccount:async()=>{throw Error('No mutation');},shieldedAddress:async()=>{throw Error('No mutation');},balance:async id=>{reads++;assert.equal(id,account);return {network:'testnet',account,confirmedSpendableZat:543210,minimumConfirmations:10,transparentBalancePresent:false};}};
 const handler=accountRoutes(c,state,fetch,async()=>undefined,backend);
 try{
  const held=await read(handler,'/account','__Host-zec_account='+alice.token);assert.ok(!held.body.includes(address));assert.ok(!held.body.includes('data:image/png;base64,'));
  deposits=true;const own=await read(handler,'/account','__Host-zec_account='+alice.token);
  store.enqueue('123','200','300','testnet',Date.now());
  const activity=await read(handler,'/account','__Host-zec_account='+alice.token);
  assert.match(activity.body,/ UTC/);assert.match(activity.body,/Resets at 00:00 UTC/);
  assert.doesNotMatch(activity.body,/Lagos|Nigeria|Africa\/|en-NG/i);
  assert.match(activity.body,/Maximum network fee \(TAZ\)/);
  assert.match(activity.body,/unused allowance is not charged/);
  assert.ok(own.body.includes(address));assert.match(own.body,/data:image\/png;base64,/);assert.match(own.body,/0\.0054321/);assert.equal(own.headers['Cache-Control'],'no-store');
  assert.match(String(own.headers['Content-Security-Policy']),/script-src 'self';/);assert.ok(!String(own.headers['Content-Security-Policy']).includes('unsafe-eval'));
  const settings=await read(handler,'/account?panel=settings','__Host-zec_account='+alice.token);
  assert.match(settings.body,/<section class="tip-settings-section" id="tip-settings">/);assert.match(settings.body,/>Tip settings<\/a>/);
  assert.doesNotMatch(settings.body,/<details[^>]+id="tip-settings"/);
  assert.equal(store.profile('200','testnet')!.enabled,0);
  const previous=reads;
  for(const [path,cookie] of [['/',''],['/account',''],['/account','__Host-zec_account='+bob.token],['/assets/private.js','']] as const){
   const page=await read(handler,path,cookie);assert.ok(!page.body.includes(address));assert.ok(!page.body.includes('0.0054321'));assert.ok(!page.body.includes(alice.csrf));
  }
  assert.equal(reads,previous);assert.equal(store.history('200','testnet').length,1);
 }finally{state.close();}
});
test('visual helpers use local actions without remote requests or stored financial data',()=>{
 assert.doesNotThrow(()=>new Script(PRIVATE_JS));
 assert.ok(!/fetch\(|XMLHttpRequest|sendBeacon|https?:\/\//.test(PRIVATE_JS));
 assert.deepEqual([...PRIVATE_JS.matchAll(/localStorage\.setItem\('([^']+)'/g)].map(m=>m[1]),['zec-private-theme']);
 assert.ok(!/\.submit\(|requestSubmit\(/.test(PRIVATE_JS));
});
