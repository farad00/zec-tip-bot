import {test} from 'node:test';
import assert from 'node:assert/strict';
import {State} from '../src/state.js';
import {TipStore} from '../src/tip-store.js';
import {loadConfig} from '../src/config.js';
import {currentSnowflake,planMention} from '../src/bot.js';
import {XApi,type Mention} from '../src/x.js';
import {testUnifiedAddress} from './wallet-test-address.js';
const config=loadConfig({PRIVATE_TIPPING_ENABLED:'true',ZCASH_NETWORK:'testnet',X_BOT_USER_ID:'100'});
function setup(){const state=new State(':memory:'),store=new TipStore(state);for(const [id,name,n] of [['200','sender',1],['300','recipient',2]] as const){store.login(id,name,'testnet');store.attachVerifiedTestnetWallet(id,'account-'+id,testUnifiedAddress(n));}store.settings('200','testnet',{amount:'0.001',daily:'0.01',fee:'0.001',enabled:true,publicAck:false,consent:true},'0.01',Date.now()-1000);return {state,store};}
function mention(text:string):Mention{const start=text.indexOf('@zectipbot');return {id:currentSnowflake(),author_id:'200',text,display_text_range:[start,text.length],entities:{mentions:[{username:'recipient',id:'300',start:text.indexOf('@recipient')},{username:'sender',id:'200',start:text.indexOf('@sender')},{username:'threadmate',id:'400',start:text.indexOf('@threadmate')},{username:'zectipbot',id:'100',start}]},referenced_tweets:[{type:'replied_to',id:'500'}]};}
test('verified reply-context mentions do not hide an exact private tip and the parent is resolved once',async()=>{
 const s=setup();let reads=0;try{
  const m=mention('@threadmate @recipient @zectipbot tip'),api={lookup:async()=>({id:'300',username:'recipient'}),postAuthor:async()=>{reads++;return {id:'300',username:'recipient'};}};
  const plan=await planMention(m,config,s.state,api);assert.equal(plan?.privateAction,true);assert.equal(plan?.text,undefined);assert.equal(s.store.job(m.id),undefined);
  plan?.apply?.();assert.equal(reads,1);assert.equal(s.store.job(m.id)!.recipient,'300');assert.equal(s.store.job(m.id)!.amount_zat,100000);
 }finally{s.state.close();}
});
test('unbound, duplicated and prose prefixes cannot authorize a tip',async()=>{
 for(const raw of ['hello @zectipbot tip','@stranger @zectipbot tip','@recipient @recipient @zectipbot tip','@recipient @zectipbot tip 0.1','@recipient @zectipbot tip.']){
  const s=setup();try{const m=mention(raw),plan=await planMention(m,config,s.state,{lookup:async()=>({id:'300',username:'recipient'}),postAuthor:async()=>({id:'300',username:'recipient'})});assert.equal(plan?.apply,undefined);assert.equal(s.store.history('200','testnet').length,0);}finally{s.state.close();}
 }
 const s=setup();try{
  const m=mention('@recipient @zectipbot tip');m.entities!.mentions![0]!.id='not-an-id';assert.equal(await planMention(m,config,s.state,{lookup:async()=>({id:'300',username:'recipient'}),postAuthor:async()=>({id:'300',username:'recipient'})}),undefined);
  delete m.entities!.mentions![0]!.id;assert.equal(await planMention(m,config,s.state,{lookup:async()=>({id:'300',username:'recipient'}),postAuthor:async()=>({id:'300',username:'recipient'})}),undefined);
 }finally{s.state.close();}
});
test('visible prefixes and truncated display ranges never become payment authorization',async()=>{
 for(const range of [undefined,[0,25],[11,24],[11.5,25]] as const){
  const s=setup();try{const m=mention('@recipient @zectipbot tip');m.display_text_range=range?[range[0],range[1]]:undefined;
   const plan=await planMention(m,config,s.state,{lookup:async()=>({id:'300',username:'recipient'}),postAuthor:async()=>({id:'300',username:'recipient'})});assert.equal(plan?.apply,undefined);
  }finally{s.state.close();}
 }
 const s=setup();try{const m=mention('@recipient @zectipbot tip 0.1');m.display_text_range=[11,25];assert.equal(await planMention(m,config,s.state,{lookup:async()=>({id:'300',username:'recipient'}),postAuthor:async()=>({id:'300',username:'recipient'})}),undefined);}finally{s.state.close();}
});
test('verified reply-context stop still disables only the command author',async()=>{
 const s=setup();try{
  const plan=await planMention(mention('@recipient @zectipbot stop'),config,s.state,{lookup:async()=>({id:'300',username:'recipient'}),postAuthor:async()=>({id:'300',username:'recipient'})});plan?.apply?.();assert.equal(s.store.profile('200','testnet')!.enabled,0);assert.equal(s.state.user('200')!.opted_out,1);assert.equal(s.store.wallet('300','testnet')!.status,'ready');
 }finally{s.state.close();}
});
test('the mention request actually asks X for the display range needed by reply parsing',async()=>{
 const post=mention('@threadmate @recipient @zectipbot tip');let checked=false;
 const api=new XApi(config,()=>{},async input=>{const url=new URL(String(input));assert.ok(url.searchParams.get('tweet.fields')!.split(',').includes('display_text_range'));checked=true;return new Response(JSON.stringify({data:[post],meta:{result_count:1}}));});
 const result=await api.mentions('100');assert.ok(checked);assert.deepEqual(result.data![0]!.display_text_range,post.display_text_range);
});
test('editing a previous tip cannot create another spend authorization',async()=>{
 const s=setup();try{
  const m=mention('@recipient @zectipbot tip');m.edit_history_tweet_ids=['111',m.id];
  const plan=await planMention(m,config,s.state,{lookup:async()=>{throw Error('must not resolve');},postAuthor:async()=>{throw Error('must not resolve');}});
  assert.equal(plan?.apply,undefined);assert.equal(s.store.history('200','testnet').length,0);assert.match(plan!.text!,/Edited tip commands/);
 }finally{s.state.close();}
});
