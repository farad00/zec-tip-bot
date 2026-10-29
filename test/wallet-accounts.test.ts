import {test} from 'node:test';
import assert from 'node:assert/strict';
import {bech32} from '@scure/base';
import {State} from '../src/state.js';
import {TipStore} from '../src/tip-store.js';
import {WalletAccountProvisioner,type AccountWalletBackend} from '../src/wallet-accounts.js';
const A='11111111-1111-4111-8111-111111111111',B='22222222-2222-4222-8222-222222222222';
const address=(n:number)=>bech32.encode('ztestsapling',bech32.toWords(Buffer.alloc(43,n)),200);
function setup(){
 const state=new State(':memory:'),store=new TipStore(state);store.login('200','alice','testnet');store.login('300','bob','testnet');
 const native:{account:string;name:string}[]=[];let uncertain=false,consensus=true,backup=true,deriveFails=false;const calls:string[]=[];
 const wallet:AccountWalletBackend={health:async()=>({network:'testnet',synced:true,currentConsensusVerified:consensus,backupConfirmed:backup,accountCreationReady:true,depositsEnabled:false,transfersEnabled:false}),
  accounts:async()=>native,
  createAccount:async name=>{calls.push(name);const result={account:native.length?B:A,name};native.push(result);if(uncertain)throw new Error('response lost');return result;},
  shieldedAddress:async(account,index)=>{assert.equal(index,0);if(deriveFails)throw new Error('temporarily unavailable');return {network:'testnet',account,address:address(account===A?1:2)};}};
 return {state,store,native,calls,wallet,provisioner:new WalletAccountProvisioner(store,wallet),loseResponse:()=>{uncertain=true;},unverified:()=>{consensus=false;},noBackup:()=>{backup=false;},failDerivation:(v:boolean)=>{deriveFails=v;}};
}
test('provisioning is idempotent, X-ID bound, uses private labels and never enables spending',async()=>{
 const s=setup();try{
  const first=await s.provisioner.provision('200');assert.equal(first.account,A);assert.equal((await s.provisioner.provision('200')).address,first.address);
  assert.equal(s.calls.length,1);assert.match(s.calls[0]!,/^tip-[a-f0-9]{32}$/);assert.ok(!s.calls[0]!.includes('alice'));
  s.store.login('200','renamed','testnet');assert.equal((await s.provisioner.provision('200')).account,A);
  assert.equal(s.store.profile('200','testnet')!.enabled,0);assert.equal(s.state.db.prepare('SELECT COUNT(*) AS n FROM tip_jobs').get()!.n,0);
 }finally{s.state.close();}
});
test('lost native creation response reconciles by persisted opaque label without another creation',async()=>{
 const s=setup();try{s.loseResponse();await assert.rejects(s.provisioner.provision('200'),/uncertain/);assert.equal(s.store.wallet('200','testnet'),undefined);
  assert.equal((await s.provisioner.provision('200')).account,A);assert.equal(s.calls.length,1);
 }finally{s.state.close();}
});
test('interrupted creation with no matching account is held; no blind mutation retry',async()=>{
 const s=setup();try{s.loseResponse();await assert.rejects(s.provisioner.provision('200'));s.native.length=0;await assert.rejects(s.provisioner.provision('200'),/reconciliation/);assert.equal(s.calls.length,1);assert.equal(s.store.wallet('200','testnet'),undefined);
 }finally{s.state.close();}
});
test('fixed-index address derivation can resume without creating another native account',async()=>{
 const s=setup();try{s.failDerivation(true);await assert.rejects(s.provisioner.provision('200'));s.failDerivation(false);assert.equal((await s.provisioner.provision('200')).account,A);assert.equal(s.calls.length,1);
 }finally{s.state.close();}
});
test('consensus or backup not verified blocks provisioning before native mutation',async()=>{
 for(const kind of ['consensus','backup']){const s=setup();try{if(kind==='consensus')s.unverified();else s.noBackup();await assert.rejects(s.provisioner.provision('200'),/verified first/);assert.equal(s.calls.length,0);assert.equal(s.native.length,0);}finally{s.state.close();}}
});
test('cross-account address response is rejected and durable global lease prevents parallel creation',async()=>{
 const s=setup();try{s.wallet.shieldedAddress=async()=>({network:'testnet',account:B,address:address(2)});await assert.rejects(s.provisioner.provision('200'),/reconciliation/);assert.equal(s.store.wallet('200','testnet'),undefined);
  s.state.db.prepare("INSERT INTO wallet_account_leases VALUES ('provision','other',?)").run(Date.now()+60000);await assert.rejects(s.provisioner.provision('300'),/busy/);assert.equal(s.calls.length,1);
 }finally{s.state.close();}
});

test('encrypted recovery admission must succeed before a deposit address is attached',async()=>{
 const s=setup();try{
  const original=s.wallet.health;s.wallet.health=async()=>({...await original(),accountAdmissionRequired:true});
  let admitted=false,attempts=0;
  s.wallet.admitAccount=async value=>{attempts++;assert.equal(value.userId,'200');assert.equal(value.account,A);assert.match(value.label,/^tip-/);if(!admitted)throw Error('backup response unavailable');};
  await assert.rejects(s.provisioner.provision('200'));
  assert.equal(s.store.wallet('200','testnet'),undefined);assert.equal(s.calls.length,1);
  admitted=true;assert.equal((await s.provisioner.provision('200')).account,A);assert.equal(attempts,2);assert.equal(s.calls.length,1);
  assert.equal(s.store.profile('200','testnet')!.enabled,0);
 }finally{s.state.close();}
});

test('recovery admission cannot be bypassed by an outdated account adapter',async()=>{
 const s=setup();try{const original=s.wallet.health;s.wallet.health=async()=>({...await original(),accountAdmissionRequired:true});
  await assert.rejects(s.provisioner.provision('200'),/admission/);assert.equal(s.calls.length,0);
 }finally{s.state.close();}
});

test('a completed last-slot creation can reconcile after general enrollment closes',async()=>{
 const s=setup();try{s.failDerivation(true);await assert.rejects(s.provisioner.provision('200'));
  const original=s.wallet.health;s.wallet.health=async()=>({...await original(),accountCreationReady:false});s.failDerivation(false);
  assert.equal((await s.provisioner.provision('200')).account,A);assert.equal(s.calls.length,1);
  await assert.rejects(s.provisioner.provision('300'));assert.equal(s.calls.length,1);
 }finally{s.state.close();}
});

test('a pending address setup resumes at the fixed shielded index without creating another account',async()=>{
 const s=setup();try{
  s.failDerivation(true);await assert.rejects(s.provisioner.provision('200'));
  const original=s.wallet.health;s.wallet.health=async()=>({...await original(),accountAdmissionRequired:true,receivingIndex:4294967296});
  s.wallet.shieldedAddress=async(account,index)=>{assert.equal(index,4294967296);return {network:'testnet',account,address:address(1)};};
  s.wallet.admitAccount=async()=>{};
  assert.equal((await s.provisioner.provision('200')).account,A);assert.equal(s.calls.length,1);
 }finally{s.state.close();}
});
