import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,realpathSync,rmSync} from 'node:fs';
import {join,dirname,basename,resolve} from 'node:path';
import {bech32} from '@scure/base';
import {State} from '../src/state.js';
import {TipStore} from '../src/tip-store.js';
import {TipEngine,type TipWalletBackend} from '../src/tip-engine.js';
const address=(n:number)=>bech32.encode('ztestsapling',bech32.toWords(Buffer.alloc(43,n)),200);
function setup(){
 const root=realpathSync(process.cwd()),folder=mkdtempSync(join(root,'data-test-spend-lease-'));
 const first=new State(join(folder,'state.sqlite')),a=new TipStore(first);
 for(const id of ['200','300']){a.login(id,'user'+id,'testnet');a.attachVerifiedTestnetWallet(id,'account-'+id,address(id==='200'?1:2));}
 a.settings('200','testnet',{amount:'0.001',daily:'0.01',fee:'0.0001',enabled:true,publicAck:false,consent:true},'1',Date.now()-1000);
 a.enqueue('1','200','300','testnet',Date.now());
 const second=new State(join(folder,'state.sqlite')),b=new TipStore(second);
 let release!:()=>void,entered!:()=>void;
 const gate=new Promise<void>(r=>{release=r;}),started=new Promise<void>(r=>{entered=r;}),calls:string[]=[];
 const wallet:TipWalletBackend={health:async()=>({network:'testnet',synced:true}),
  prepare:async input=>{calls.push('prepare');entered();await gate;return {network:'testnet',account:input.account,recipient:input.recipient,amountZat:input.amountZat,feeZat:10000,privacy:'FullPrivacy',transparentInputs:0,transparentOutputs:0,proposal:'synthetic-test-proposal'};},
  sign:async()=>{calls.push('sign');return {txid:'a'.repeat(64),raw:'abcd'};},
  broadcast:async()=>{calls.push('broadcast');return {txid:'a'.repeat(64)};},confirmations:async()=>0};
 return {a,b,first,second,calls,release,started,one:new TipEngine(a,wallet),two:new TipEngine(b,wallet),
  close:()=>{first.close();second.close();const target=realpathSync(folder);assert.equal(dirname(target),root);assert.ok(basename(target).startsWith('data-test-spend-lease-'));rmSync(target,{recursive:true,force:true});}};
}

test('separate database connections cannot interrupt or duplicate an active wallet operation',async()=>{
 const s=setup();try{
  const work=s.one.process('1');await s.started;
  assert.equal(s.b.job('1')!.status,'preparing');await s.two.process('1');
  assert.equal(s.b.job('1')!.status,'preparing');assert.deepEqual(s.calls,['prepare']);
  s.release();await work;assert.equal(s.b.job('1')!.status,'submitted');assert.deepEqual(s.calls,['prepare','sign','broadcast']);
 }finally{s.close();}
});

test('expired owner cannot persist its late proposal or erase a replacement lease',async()=>{
 const s=setup();try{
  const work=s.one.process('1');await s.started;
  s.second.db.prepare("UPDATE tip_engine_leases SET expires=? WHERE name='spend'").run(Date.now()-1);
  await s.two.process('1');assert.equal(s.b.job('1')!.status,'review');
  s.second.db.prepare("INSERT INTO tip_engine_leases VALUES ('spend','replacement',?)").run(Date.now()+90_000);
  s.release();await work;
  assert.deepEqual(s.calls,['prepare']);assert.equal(s.b.job('1')!.status,'review');
  assert.equal(s.second.db.prepare('SELECT COUNT(*) AS n FROM tip_spends').get()!.n,0);
  assert.equal(s.second.db.prepare("SELECT owner FROM tip_engine_leases WHERE name='spend'").get()!.owner,'replacement');
 }finally{s.close();}
});

test('distinct commands share the durable spend lease rather than preparing overlapping wallet spends',async()=>{
 const s=setup();try{
  s.a.enqueue('2','200','300','testnet',Date.now());
  const work=s.one.process('1');await s.started;await s.two.process('2');
  assert.equal(s.b.job('2')!.status,'queued');assert.deepEqual(s.calls,['prepare']);
  s.release();await work;await s.two.process('2');assert.equal(s.b.job('2')!.status,'submitted');
  assert.equal(s.calls.filter(x=>x==='prepare').length,2);
 }finally{s.close();}
});
