import {test} from 'node:test';
import assert from 'node:assert/strict';
import {State} from '../src/state.js';
import {TipStore} from '../src/tip-store.js';
import {loadConfig} from '../src/config.js';
import {currentSnowflake,planMention,runBot} from '../src/bot.js';
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

test('opted-in replies acknowledge receipt without revealing a payment outcome or financial terms',async()=>{
 const s=setup();try{
  s.store.settings('200','testnet',{amount:'0.001',daily:'0.01',fee:'0.001',enabled:true,publicAck:true,consent:true},'0.01',Date.now()-1000);
  const m=mention('@recipient @zectipbot tip'),api={lookup:async()=>({id:'300',username:'recipient'}),postAuthor:async()=>({id:'300',username:'recipient'})};
  const plan=await planMention(m,config,s.state,api);
  assert.equal(plan?.text,`Command received. Check your private wallet for status: ${config.baseUrl}/account`);
  assert.equal(s.store.job(m.id),undefined);plan?.apply?.();
  assert.equal(s.store.job(m.id)!.status,'queued');
  assert.doesNotMatch(plan!.text!,/sent|confirmed|failed|0\.001|TAZ|utest1/i);
  s.state.db.prepare("UPDATE tip_jobs SET status='confirmed' WHERE source=?").run(m.id);
  const after=await planMention(m,config,s.state,api);assert.equal(after?.text,plan?.text);
 }finally{s.state.close();}
});

test('missing recipient wallet gives the same public acknowledgement and never pays after a later signup',async()=>{
 const s=setup();try{
  s.store.settings('200','testnet',{amount:'0.001',daily:'0.01',fee:'0.001',enabled:true,publicAck:true,consent:true},'0.01',Date.now()-1000);
  const m=mention('@recipient @zectipbot tip'),api={lookup:async()=>({id:'400',username:'newperson'}),postAuthor:async()=>({id:'400',username:'newperson'})};
  const plan=await planMention(m,config,s.state,api);assert.equal(plan!.text!,`Command received. Check your private wallet for status: ${config.baseUrl}/account`);assert.doesNotMatch(plan!.text!,/not registered|no wallet|failed|sent/);
  plan?.apply?.();assert.equal(s.store.job(m.id)!.status,'failed');assert.equal(s.store.job(m.id)!.reserved_zat,0);
  s.store.login('400','newperson','testnet');const again=await planMention(m,config,s.state,api);again?.apply?.();
  assert.equal(s.store.job(m.id)!.status,'failed');assert.equal(s.store.job(m.id)!.reserved_zat,0);
 }finally{s.state.close();}
});


test('new, incomplete and disabled senders receive identical setup guidance without revealing registration or financial terms',async()=>{
 for(const stage of ['new','signed_in','wallet_created','disabled'] as const){
  const state=new State(':memory:'),store=new TipStore(state);
  try {
   if(stage!=='new')store.login('200','sender','testnet');
   if(stage==='wallet_created'||stage==='disabled')store.attachVerifiedTestnetWallet('200','sender-wallet',testUnifiedAddress(1));
   if(stage==='disabled')store.settings('200','testnet',{amount:'0.001',daily:'0.01',fee:'0.001',enabled:false,publicAck:false,consent:true},'0.01');
   const before=store.profile('200','testnet'),m=mention('@zectipbot tip @recipient');
   const plan=await planMention(m,config,state,{lookup:async()=>({id:'300',username:'recipient'})});
   assert.equal(plan?.text,`Configure and enable tipping first: ${config.baseUrl}/account`);
   assert.doesNotMatch(plan!.text!,/create a wallet|not registered|balance|0\.001|TAZ|sent|confirmed/i);
   assert.equal(store.job(m.id),undefined);plan!.apply!();
   assert.equal(store.job(m.id)!.status,'failed');assert.equal(store.job(m.id)!.reason,'setup_required');assert.equal(store.job(m.id)!.reserved_zat,0);
   assert.deepEqual(store.profile('200','testnet'),before);
  }finally{state.close();}
 }
});

test('explicit stop suppresses setup guidance; start resumes replies without enabling payments',async()=>{
 const {state,store}=setup();try{
  store.disable('200','testnet');state.opt('200',true);
  const api={lookup:async()=>{throw Error('opted out: no X lookup');}};
  assert.equal(await planMention(mention('@zectipbot tip @recipient'),config,state,api),undefined);
  const start=await planMention(mention('@zectipbot start'),config,state,api);start!.apply!();
  assert.equal(store.profile('200','testnet')!.enabled,0);
  const p=await planMention(mention('@zectipbot tip @recipient'),config,state,{lookup:async()=>({id:'300',username:'recipient'})});
  assert.match(p!.text!,/^Configure and enable tipping first:/);
 }finally{state.close();}
});

test('enabled sender public-reply preference still suppresses financial and recipient setup acknowledgements',async()=>{
 const {state,store}=setup();try{
  for(const id of ['300','400']){
   const plan=await planMention(mention('@zectipbot tip @recipient'),config,state,{lookup:async()=>({id,username:'recipient'})});
   assert.equal(plan!.text,undefined);assert.equal(store.history('200','testnet').length,0);
  }
 }finally{state.close();}
});

test('a failed setup command gets one actual guidance reply and cannot send or replay a tip',async()=>{
 const state=new State(':memory:'),store=new TipStore(state),post=mention('@zectipbot tip @recipient');
 const c={...config,posting:true,apiKey:'test-key',apiSecret:'test-secret',accessToken:'test-token',accessSecret:'test-token-secret'},writes:string[]=[];
 const api=new XApi(c,()=>{},(async(url,options)=>{
  const target=String(url);
  if(options?.method==='POST'){
   const body=JSON.parse(String(options.body));assert.equal(body.reply.in_reply_to_tweet_id,post.id);writes.push(body.text);
   return Response.json({data:{id:'900'}});
  }
  if(target.includes('/mentions?'))return Response.json({data:[post]});
  if(target.includes('/by/username/'))return Response.json({data:{id:'300',username:'recipient'}});
  if(target.endsWith('/users/me'))return Response.json({data:{id:'100',username:'zectipbot'}});
  throw Error('Unexpected request');
 }) as typeof fetch);
 try{
  state.set('since_id','1');await runBot(c,state,api,true,{quiet:true});
  assert.deepEqual(writes,[`Configure and enable tipping first: ${c.baseUrl}/account`]);
  assert.equal(store.job(post.id)!.status,'failed');assert.equal(store.job(post.id)!.reason,'setup_required');assert.equal(store.job(post.id)!.reserved_zat,0);
  assert.equal(store.profile('200','testnet'),undefined);assert.equal(state.attempted(post.id),true);
  await runBot(c,state,api,true,{quiet:true});assert.equal(writes.length,1);
 }finally{state.close();}
});
