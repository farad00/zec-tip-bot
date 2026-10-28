import { test } from 'node:test';
import assert from 'node:assert/strict';
import { BudgetLimitError, DailyReplyLimitError, State } from '../src/state.js';
import { loadConfig } from '../src/config.js';
import { XApi, XError, XRequestNotSentError } from '../src/x.js';
import { watch, workerDecision } from '../src/worker.js';

const config = () => loadConfig({ X_API_KEY:'dummy', X_API_SECRET:'dummy', X_ACCESS_TOKEN:'dummy', X_ACCESS_SECRET:'dummy', X_BOT_USER_ID:'100', X_POSTING_ENABLED:'true', PUBLIC_BASE_URL:'https://tips.example.org', POLL_INTERVAL_SECONDS:'30' });
test('automatic polling processes a new command once across repeated checks and releases its lease on shutdown', async () => {
  const state = new State(':memory:'); state.set('since_id','1');
  const controller = new AbortController();
  let polls = 0, writes = 0, identityReads = 0, sleeps = 0;
  const api = () => new XApi(config(), () => {}, (async (url, init) => {
    if (init?.method === 'POST') { writes++; return Response.json({ data:{id:'300'} }); }
    if (String(url).includes('/mentions?')) { polls++; return Response.json({data:[{id:'2',author_id:'200',text:'@zectipbot help',entities:{mentions:[{id:'100',username:'zectipbot'}]}}]}); }
    identityReads++; return Response.json({data:{id:'100',username:'zectipbot'}});
  }) as typeof fetch);
  try {
    await watch(config(),state,{ signal:controller.signal, createApi:api, log:()=>{}, sleep:async()=>{ if(++sleeps===3)controller.abort(); }});
    assert.equal(polls,3); assert.equal(writes,1);
    assert.equal(identityReads,2); // initial identity plus fresh verification immediately before the one write
    assert.equal(state.get('since_id'),'2');
    state.acquire('bot'); state.release('bot');
  } finally { state.close(); }
});
test('daily budget and reply limits pause until the next UTC day, including wrapped errors', () => {
  const now = Date.parse('2026-10-27T23:00:00Z');
  const decision = workerDecision(new XRequestNotSentError('local guard',{cause:new BudgetLimitError('guard')}),1,30000,now);
  assert.equal(decision.stop,false); assert.equal(decision.delayMs,3605000);
  assert.equal(workerDecision(new DailyReplyLimitError('guard'),1,30000,now).delayMs,3605000);
});
test('rate limits respect server reset times and access failures stop automatic operation', () => {
  const now = Date.now();
  const limited = workerDecision(new XError(429,now+120000),1,30000,now);
  assert.equal(limited.delayMs,121000); assert.equal(limited.stop,false);
  for(const status of [401,402,403]) assert.equal(workerDecision(new XError(status),1,30000,now).stop,true);
  assert.equal(workerDecision(new Error('network'),3,30000,now).stop,true);
});
test('empty mention reads settle their local reservation to zero; malformed or failed responses remain reserved', async () => {
  for(const [body,status,expected] of [[{meta:{result_count:0}},200,0],[{data:[]},200,0],[{data:[{id:'2'}]},200,5],[{unexpected:true},200,50],[{errors:[{}]},500,50]] as const) {
    const s = new State(':memory:');
    try {
      const api = new XApi(config(), cost=>s.reserveRequest(cost,1000),(async()=>Response.json(body,{status})) as typeof fetch);
      try { await api.mentions('100','1'); } catch {}
      assert.equal((s.status().usage[0] as { mills:number }).mills,expected);
    } finally{s.close();}
  }
});
test('reservation settlement is idempotent and cannot refund more than reserved', () => {
  const s = new State(':memory:');
  try {
    const settle = s.reserveRequest(50,1000); settle(-1); settle(51); settle(5); settle(0);
    assert.equal((s.status().usage[0] as {mills:number}).mills,5);
  } finally{s.close();}
});
test('worker refuses disabled posting or an invalid public URL before making any X calls', async () => {
  const s=new State(':memory:'); const controller=new AbortController();
  let calls=0;
  try{
    const c=config(); c.posting=false;
    await assert.rejects(watch(c,s,{signal:controller.signal,createApi:()=>{calls++;throw new Error();}}),/POSTING_ENABLED/);
    assert.equal(calls,0);
  }finally{s.close();}
});

test('shutdown during a write leaves an unknown attempt protected and releases the batch lease',async()=>{
 const s=new State(':memory:');s.set('since_id','1');const controller=new AbortController();
 const api=()=>new XApi(config(),()=>{},(async(url,init)=>{
  if(init?.method==='POST'){controller.abort();throw new Error('interrupted');}
  if(String(url).includes('/mentions?'))return Response.json({data:[{id:'2',author_id:'200',text:'@zectipbot help',entities:{mentions:[{id:'100',username:'zectipbot'}]}}]});
  return Response.json({data:{id:'100',username:'zectipbot'}});
 })as typeof fetch,undefined,controller.signal);
 try{
  await watch(config(),s,{signal:controller.signal,createApi:api,log:()=>{},sleep:async()=>{}});
  assert.equal(s.attempted('2'),true);assert.equal((s.status().unresolved[0]as{status:string}).status,'unknown');
  s.acquire('bot');s.release('bot');
 }finally{s.close();}
});

test('a fatal worker error is held durably so a hosting restart cannot repeat paid X startup calls',async()=>{
 const state=new State(':memory:');state.set('since_id','1');const controller=new AbortController();let reads=0;
 const c=config();
 const make=()=>new XApi(c,()=>{},(async()=>{reads++;return new Response('private upstream',{status:403});})as typeof fetch);
 try{
  await assert.rejects(watch(c,state,{signal:controller.signal,createApi:make,log:()=>{},sleep:async()=>{}}),/access requires attention/);
  assert.equal(state.status().workerHeld,true);assert.equal(reads,1);
  await assert.rejects(watch(c,state,{signal:controller.signal,createApi:make,log:()=>{}}),/held after a fatal/);assert.equal(reads,1);
  state.set('worker_hold','');assert.equal(state.status().workerHeld,false);
 }finally{state.close();}
});
