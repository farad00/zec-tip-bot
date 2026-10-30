import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Readable} from 'node:stream';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {createHash} from 'node:crypto';
import {State} from '../src/state.js';
import {TipStore} from '../src/tip-store.js';
import {TipEngine,ProvenSpendRejection,type TipWalletBackend} from '../src/tip-engine.js';
import {accountRoutes} from '../src/account.js';
import {loadConfig} from '../src/config.js';
import {installWalletLive} from '../src/live-ui.js';
import type {AccountWalletBackend} from '../src/wallet-accounts.js';
import {testUnifiedAddress} from './wallet-test-address.js';

const config=loadConfig({PRIVATE_TIPPING_ENABLED:'true',ZCASH_NETWORK:'testnet',PUBLIC_BASE_URL:'https://tips.example.org'});
const A='11111111-1111-4111-8111-111111111111',B='22222222-2222-4222-8222-222222222222';
const health={network:'testnet' as const,synced:true,currentConsensusVerified:true,backupConfirmed:true,accountCreationReady:true,depositsEnabled:true,transfersEnabled:true,minimumConfirmations:3 as const};
async function read(handler:ReturnType<typeof accountRoutes>,path:string,cookie='',site?:string){
 const req=Readable.from([]) as IncomingMessage;req.url=path;req.method='GET';req.headers={host:'tips.example.org',cookie,...(site?{'sec-fetch-site':site}:{})};
 let body='',status=200;const headers:Record<string,unknown>={};
 const res={setHeader:(key:string,value:unknown)=>{headers[key]=value;},writeHead:(code:number)=>{status=code;return res;},end:(value?:string)=>{body=value??'';return res;}} as unknown as ServerResponse;
 await handler(req,res);return {status,body,headers};
}
function setup(){
 const state=new State(':memory:'),store=new TipStore(state),alice=store.login('200','alice','testnet'),bob=store.login('300','bob','testnet');
 store.attachVerifiedTestnetWallet('200',A,testUnifiedAddress(1));store.attachVerifiedTestnetWallet('300',B,testUnifiedAddress(2));
 store.settings('200','testnet',{amount:'0.01',fee:'0.001',daily:'0.02',enabled:true,publicAck:true,consent:true},'0.01',Date.now()-1000);
 let available=700000,pending=0,reads=0;
 const native:AccountWalletBackend={health:async()=>health,accounts:async()=>[],createAccount:async()=>{throw Error('No mutation');},shieldedAddress:async()=>{throw Error('No derivation');},
  balance:async account=>{reads++;return {network:'testnet',account,confirmedSpendableZat:account===A?available:987654,minimumConfirmations:3,pendingZat:pending,transparentBalancePresent:false};},
  receipts:async account=>({network:'testnet',account,receipts:[]})};
 return {state,store,alice,bob,native,set:(a:number,p=0)=>{available=a;pending=p;},reads:()=>reads};
}

test('live snapshot is authenticated, owner-scoped, no-store and does not create withdrawal forms',async()=>{
 const s=setup(),handler=accountRoutes(config,s.state,fetch,async()=>undefined,s.native),cookie='__Host-zec_account='+s.alice.token;
 try{
  const page=await read(handler,'/account',cookie);assert.match(page.body,/private\.js\?v=4/);assert.match(page.body,/saved default tip exceeds/);
  const key=/data-live-key="([a-f0-9]{64})"/.exec(page.body)![1]!;
  const before=s.state.db.prepare('SELECT * FROM tip_withdrawal_forms').all();
  for(let i=0;i<3;i++){
   const response=await read(handler,'/account/live',cookie,'same-origin'),data=JSON.parse(response.body);
   assert.equal(response.status,200);assert.equal(response.headers['Cache-Control'],'no-store');assert.equal(response.headers['X-Robots-Tag'],'noindex, nofollow');
   assert.match(String(response.headers['Content-Security-Policy']),/connect-src 'self'/);assert.equal(data.balance,'0.007');assert.equal(data.liveKey,key);
   assert.doesNotMatch(response.body,new RegExp([s.alice.csrf,s.alice.token,A,testUnifiedAddress(1)].join('|')));
  }
  assert.deepEqual(s.state.db.prepare('SELECT * FROM tip_withdrawal_forms').all(),before);
  const calls=s.reads();assert.equal((await read(handler,'/account/live')).status,401);assert.equal(s.reads(),calls);
  assert.equal((await read(handler,'/account/live?account='+B,cookie)).status,403);
  assert.equal((await read(handler,'/account/live',cookie,'cross-site')).status,403);
  const bob=JSON.parse((await read(handler,'/account/live','__Host-zec_account='+s.bob.token)).body);assert.equal(bob.balance,'0.00987654');assert.notEqual(bob.liveKey,key);
  s.set(600000,100000);const changed=JSON.parse((await read(handler,'/account/live',cookie)).body);assert.equal(changed.balance,'0.006');assert.match(changed.pendingHtml,/0\.001 TAZ/);
 }finally{s.state.close();}
});

test('receipt deduplication hides only the owner tip already shown, and includes an incoming hosted withdrawal',async()=>{
 const s=setup();try{
  s.state.db.exec('CREATE TABLE tip_spends(source TEXT PRIMARY KEY,proposal TEXT,signed TEXT,txid TEXT)');
  const now=Date.now(),job=s.store.enqueue('123','200','300','testnet',now,now),txid='a'.repeat(64),receipt=createHash('sha256').update('zec-tip-bot:receipt:'+txid).digest('hex');
  s.state.db.prepare("UPDATE tip_jobs SET status='confirmed' WHERE source=?").run(job.source);s.state.db.prepare('INSERT INTO tip_spends(source,txid) VALUES (?,?)').run(job.source,txid);
  const other='b'.repeat(64),deposit=createHash('sha256').update('zec-tip-bot:receipt:'+other).digest('hex');
  const withdrawal=s.store.enqueueWithdrawal('200',s.store.withdrawalForm('200'),testUnifiedAddress(2),'0.001','0.001');
  s.state.db.prepare("UPDATE tip_jobs SET status='confirmed' WHERE source=?").run(withdrawal.source);s.state.db.prepare('INSERT INTO tip_spends(source,txid) VALUES (?,?)').run(withdrawal.source,other);
  assert.deepEqual([...s.store.receiptIdsInHistory('300','testnet')],[receipt]);assert.equal(s.store.receiptIdsInHistory('200','testnet').has(deposit),false);
  s.native.receipts=async account=>({network:'testnet',account,receipts:[{receipt,amountZat:1000000,confirmations:4,time:1700000000},{receipt:deposit,amountZat:100000,confirmations:4,time:1700000001}]});
  const handler=accountRoutes(config,s.state,fetch,async()=>undefined,s.native),page=await read(handler,'/account/live','__Host-zec_account='+s.bob.token),data=JSON.parse(page.body);
  assert.equal((data.activityHtml.match(/Deposit received/g)??[]).length,1);assert.match(data.activityHtml,/From @alice/);assert.match(data.activityHtml,/\+0\.001 TAZ/);
  assert.doesNotMatch(page.body,/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa|bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/);
 }finally{s.state.close();}
});

test('insufficient funds fails before signing, preserves the saved preset and shows an actionable private reason',async()=>{
 const s=setup();let signed=0;try{
  const now=Date.now(),job=s.store.enqueue('123','200','300','testnet',now,now);
  const wallet:TipWalletBackend={health:async()=>({network:'testnet',synced:true}),prepare:async()=>{throw new ProvenSpendRejection('Rejected','insufficient_funds');},
   sign:async()=>{signed++;throw Error('Must not sign');},broadcast:async()=>{throw Error('Must not broadcast');},confirmations:async()=>0};
  await new TipEngine(s.store,wallet).process(job.source);assert.equal(signed,0);assert.equal(s.store.job(job.source)!.status,'failed');assert.equal(s.store.job(job.source)!.reason,'insufficient_funds');
  assert.equal(s.store.profile('200','testnet')!.default_zat,1000000);
  const page=await read(accountRoutes(config,s.state,fetch,async()=>undefined,s.native),'/account/live','__Host-zec_account='+s.alice.token);
  assert.match(JSON.parse(page.body).activityHtml,/Insufficient available funds/);assert.match(JSON.parse(page.body).activityHtml,/Not sent/);
 }finally{s.state.close();}
});

function browser(){
 const entries=new Map<string,any>(),timers=new Map<number,{fn:()=>unknown;delay:number}>(),events=new Map<string,()=>void>();let serial=0;
 for(const selector of ['[data-live-status]','[data-balance-value]','[data-balance-toggle]','[data-live-balance-hint]','[data-live-wallet-state]','[data-live-daily-values]','[data-live-pending]','[data-live-welcome]','[data-live-activity]','[data-live-tip-warning]','.budget-meter'])
  entries.set(selector,{textContent:'old',innerHTML:'old',dataset:{},style:{},getAttribute:()=> 'false',setAttribute:()=>{},querySelector:()=>undefined});
 const page={dataset:{liveKey:'a'.repeat(64)},querySelector:(s:string)=>entries.get(s),replaceChildren:(value:unknown)=>{entries.clear();(page as any).replacement=value;}};
 const doc={hidden:false,querySelector:()=>page,addEventListener:(event:string,fn:()=>void)=>events.set(event,fn),createElement:()=>({append:(...args:unknown[])=>{},textContent:'',href:'',className:''})};
 let calls=0,response=()=>Response.json({liveKey:'a'.repeat(64),balance:'0.006',balanceHint:'Confirmed Testnet ZEC',pendingHtml:'pending',welcomeHtml:'',activityHtml:'confirmed',walletStatus:'Automatic tips on',dailyValues:'0 / 0.02 TAZ',dailyPercent:0,tipWarningHtml:''});
 const win={setTimeout:(fn:()=>unknown,delay:number)=>{timers.set(++serial,{fn,delay});return serial;},clearTimeout:(id:number)=>timers.delete(id),addEventListener:(event:string,fn:()=>void)=>events.set(event,fn),
  fetch:async(path:string,options:RequestInit)=>{calls++;assert.equal(path,'/account/live');assert.equal(options.credentials,'same-origin');assert.equal(options.cache,'no-store');assert.equal(options.redirect,'error');return response();}};
 installWalletLive(win as unknown as Window,doc as unknown as Document);
 return {entries,timers,events,page,doc,calls:()=>calls,response:(fn:()=>Response)=>{response=fn;},tick:async()=>{const next=[...timers].find(([,v])=>v.delay>=15000)!;timers.delete(next[0]);await next[1].fn();}};
}

test('automatic browser updates preserve balance masking and never touch form inputs',async()=>{
 const b=browser();b.entries.get('[data-balance-toggle]').getAttribute=()=> 'true';
 await b.tick();assert.equal(b.calls(),1);assert.equal(b.entries.get('[data-balance-value]').textContent,'••••••');assert.equal(b.entries.get('[data-balance-value]').dataset.balanceValue,'0.006');
 assert.equal(b.entries.get('[data-live-activity]').innerHTML,'confirmed');assert.equal(b.entries.get('[data-live-status]').textContent,'Auto-updates on');assert.equal((b.page as any).replacement,undefined);
 b.doc.hidden=true;await b.tick();assert.equal(b.calls(),1);
});

test('expired or changed sign-in clears private content; transient errors retain the last view and back off',async()=>{
 for(const response of [()=>new Response('{}',{status:401}),()=>Response.json({liveKey:'different'})]){
  const b=browser();b.response(response);await b.tick();assert.equal(b.entries.size,0);assert.ok((b.page as any).replacement);assert.equal(b.timers.size,0);
 }
 const b=browser();b.response(()=>new Response('{}',{status:503}));await b.tick();assert.equal(b.entries.get('[data-live-status]').textContent,'Reconnecting…');
 assert.equal(b.entries.get('[data-balance-value]').textContent,'old');assert.ok([...b.timers.values()].some(t=>t.delay===30000));
});
