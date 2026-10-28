import { test } from 'node:test';
import assert from 'node:assert/strict';
import { State } from '../src/state.js';
import { loadConfig, requirePublic } from '../src/config.js';
import { parseCommand, planMention, runBot } from '../src/bot.js';
import { XApi, XError, type Mention } from '../src/x.js';
import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { bech32 } from '@scure/base';
const address = bech32.encode('zs', bech32.toWords(Buffer.from('d8ef8293d26de832e7193f296ba1922d90f122c6135bc231eebd91efdb03b1a8606771cd4fd6480574d43e', 'hex')), 200);
const config = () => loadConfig({ X_API_KEY: 'app', X_API_SECRET: 'appsecret', X_ACCESS_TOKEN: 'user', X_ACCESS_SECRET: 'usersecret', X_BOT_USER_ID: '100', X_POSTING_ENABLED: 'true', PUBLIC_BASE_URL: 'https://tips.example.org' });
const mention = (id: string, text: string, author = '200'): Mention => ({ id, text, author_id: author, entities: { mentions: [{ username: 'zectipbot', id: '100' }] } });
test('parse only exact commands; reject prose, overrides, exponent amounts and keys', () => {
  assert.deepEqual(parseCommand('@zectipbot request @Alice 0.01 ZEC'), { type: 'request', recipient: 'alice', amount: '0.01' });
  assert.equal(parseCommand('I saw @zectipbot request @alice 1 ZEC'), undefined);
  assert.equal(parseCommand('@zectipbot request @alice 1e6 ZEC')?.type, 'invalid');
  assert.equal(parseCommand('@zectipbot request @alice 1 ZEC address=attacker')?.type, 'invalid');
  assert.equal(parseCommand('@zectipbot register seed words here public')?.type, 'invalid');
  assert.equal(parseCommand('@zectipbot register ABC')?.type, 'invalid');
});
test('wrong account, changed account ID, and disabled writes never call POST', async () => {
  for (const user of [{ id: '100', username: 'farad_0' }, { id: '999', username: 'zectipbot' }]) {
    const methods: string[] = [];
    const api = new XApi(config(), () => {}, (async (_u, options) => { methods.push(options?.method ?? 'GET'); return Response.json({ data: user }); }) as typeof fetch);
    await assert.rejects(api.reply('123', 'test'), /pinned/); assert.deepEqual(methods, ['GET']);
  }
  const c = config(); c.posting = false;
  const api = new XApi(c, () => {}, (async () => { throw new Error('must not run'); }) as typeof fetch);
  await assert.rejects(api.reply('123', 'test'), /disabled/);
});
test('every reply verifies identity and sends an actual reply payload; JSON is not signed as form data', async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const api = new XApi(config(), () => {}, (async (url, init) => {
    calls.push({ url: String(url), init });
    return Response.json({ data: init?.method === 'POST' ? { id: '300' } : { id: '100', username: 'zectipbot' } });
  }) as typeof fetch);
  assert.equal(await api.reply('123', 'Requested reply'), '300');
  assert.equal(calls[0]?.url, 'https://api.x.com/2/users/me');
  assert.deepEqual(JSON.parse(String(calls[1]?.init?.body)), { text: 'Requested reply', reply: { in_reply_to_tweet_id: '123' } });
});
test('registration uses the author ID and explicit public consent; opt-out deletes current registration', async () => {
  const s = new State(':memory:'); const c = config(); const api = { lookup: async () => ({ id: '200', username: 'alice' }) };
  try {
    const p = await planMention(mention('1', `@zectipbot register ${address} public`), c, s, api);
    assert.equal(s.user('200'), undefined); p?.apply?.(); assert.equal(s.user('200')?.address, address);
    (await planMention(mention('2', '@zectipbot stop'), c, s, api))?.apply?.();
    assert.equal(s.user('200')?.address, null); assert.equal(s.user('200')?.opted_out, 1);
    assert.equal(await planMention(mention('3', '@zectipbot help'), c, s, api), undefined);
    (await planMention(mention('4', '@zectipbot start'), c, s, api))?.apply?.(); assert.equal(s.user('200')?.opted_out, 0);
  } finally { s.close(); }
});
test('tip resolves the current username to numeric ID, never a stale username mapping', async () => {
  const s = new State(':memory:'); const c = config(); s.register('400', address);
  try {
    const p = await planMention(mention('10', '@zectipbot request @alice 0.01 ZEC'), c, s, { lookup: async () => ({ id: '400', username: 'alice' }) });
    assert.equal(p?.payment?.address, address); assert.equal(p?.payment?.amount, '0.01');
    const changed = await planMention(mention('11', '@zectipbot request @alice 0.01 ZEC'), c, s, { lookup: async () => ({ id: '401', username: 'alice' }) });
    assert.equal(changed?.payment, undefined); assert.match(changed?.text ?? '', /no active/);
    const noEntity = { ...mention('12', '@zectipbot help'), entities: undefined };
    assert.equal(await planMention(noEntity, c, s, { lookup: async () => { throw new Error(); } }), undefined);
  } finally { s.close(); }
});
test('daily spending guard, daily replies and duplicate reservation persist safely', () => {
  const s = new State(':memory:');
  try {
    s.reserveCost(200, 250); s.reserveCost(50, 250); assert.throws(() => s.reserveCost(1, 250), /spending/);
    s.reserveReply('1', 2); assert.throws(() => s.reserveReply('1', 2)); s.reserveReply('2', 2); assert.throws(() => s.reserveReply('3', 2), /Daily/);
    s.uncertain('1'); assert.equal(s.attempted('1'), true);
    s.acquire('bot'); assert.throws(() => s.acquire('bot'), /already running/); s.release('bot'); s.acquire('bot');
  } finally { s.close(); }
});
test('network-uncertain write is not retried after restart; later commands can progress', async () => {
  const s = new State(':memory:'); s.set('since_id', '1'); let posts = 0;
  const api = new XApi(config(), () => {}, (async (url, options) => {
    if (options?.method === 'POST') { posts++; throw new Error('timeout'); }
    if (String(url).includes('/mentions?')) return Response.json({ data: [mention('2', '@zectipbot help')] });
    return Response.json({ data: { id: '100', username: 'zectipbot' } });
  }) as typeof fetch);
  try {
    await assert.rejects(runBot(config(), s, api, true), /network/); assert.equal(s.attempted('2'), true);
    await runBot(config(), s, api, true); assert.equal(posts, 1); assert.equal(s.get('since_id'), '2');
  } finally { s.close(); }
});
test('preview creates no registration, request, attempt or cursor changes', async () => {
  const s = new State(':memory:'); s.set('since_id', '1'); let posts = 0;
  const api = new XApi(config(), () => {}, (async (url, options) => {
    if (options?.method === 'POST') posts++;
    if (String(url).includes('/mentions?')) return Response.json({ data: [mention('2', `@zectipbot register ${address} public`)] });
    return Response.json({ data: { id: '100', username: 'zectipbot' } });
  }) as typeof fetch);
  try { await runBot(config(), s, api, false); assert.equal(posts, 0); assert.equal(s.user('200'), undefined); assert.equal(s.attempted('2'), false); assert.equal(s.get('since_id'), '1'); }
  finally { s.close(); }
});
test('pagination is fully read before advancing; incomplete backlog stops without writes', async () => {
  const s = new State(':memory:'); s.set('since_id', '1'); const c = config(); c.maxPages = 1;
  const api = new XApi(c, () => {}, (async (url) => Response.json(String(url).includes('/mentions?') ? { data: [mention('2', '@zectipbot help')], meta: { next_token: 'more' } } : { data: { id: '100', username: 'zectipbot' } })) as typeof fetch);
  try { await assert.rejects(runBot(c, s, api, true), /backlog/); assert.equal(s.get('since_id'), '1'); assert.equal(s.attempted('2'), false); }
  finally { s.close(); }
});
test('live payment links require HTTPS; rate limits never trigger automatic retry or leak bodies', async () => {
  assert.throws(() => requirePublic(loadConfig({})), /HTTPS/);
  let calls = 0;
  const api = new XApi(config(), () => {}, (async () => { calls++; return new Response('SECRET_UPSTREAM', { status: 429 }); }) as typeof fetch);
  await assert.rejects(api.me(), e => e instanceof Error && /rate limit/.test(e.message) && !e.message.includes('SECRET_UPSTREAM')); assert.equal(calls, 1);
});
test('full bot flow registers an account, creates one immutable request and replies once', async () => {
  const s = new State(':memory:'); s.set('since_id', '1');
  let posts = 0; const bodies: { text: string; reply: { in_reply_to_tweet_id: string } }[] = [];
  const c = config(); c.paymentCards = false;
  const api = new XApi(c, () => {}, (async (url, init) => {
    if (init?.method === 'POST') { posts++; bodies.push(JSON.parse(String(init.body))); return Response.json({ data: { id: String(300 + posts) } }); }
    if (String(url).includes('/mentions?')) return Response.json({ data: [mention('3', '@zectipbot request @alice 0.01 ZEC', '500'), mention('2', `@zectipbot register ${address} public`, '400')] });
    if (String(url).includes('/by/username/')) return Response.json({ data: { id: '400', username: 'alice' } });
    return Response.json({ data: { id: '100', username: 'zectipbot' } });
  }) as typeof fetch);
  try {
    await runBot(c, s, api, true);
    assert.equal(posts, 2); assert.equal(s.get('since_id'), '3');
    const text = bodies[1]!.text; assert.ok(!text.includes('@alice'));
    const id = /\/p\/([a-f0-9]{32})/.exec(text)?.[1]; assert.ok(id);
    const p = s.payment(id); assert.equal(p?.address, address); assert.equal(p?.amount, '0.01');
    s.opt('400', true); assert.equal(s.payment(id)?.address, address); // shared requests are immutable
    await runBot(c, s, api, true); assert.equal(posts, 2);
  } finally { s.close(); }
});
test('uncertain attempts and budgets survive closing and reopening the actual database', () => {
  const dir = mkdtempSync(join(process.cwd(), 'data-test-restart-')); const path = join(dir, 'bot.sqlite');
  try {
    const first = new State(path); first.set('since_id', '20'); first.reserveReply('21', 10); first.reserveCost(200, 250); first.close();
    const second = new State(path);
    try { assert.equal(second.attempted('21'), true); assert.equal(second.get('since_id'), '20'); assert.throws(() => second.reserveCost(51, 250)); }
    finally { second.close(); }
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
test('reply cap advances only through processed posts; later commands remain available', async () => {
  const s = new State(':memory:'); s.set('since_id', '1'); const c = config(); c.maxReplies = 1;
  const api = new XApi(c, () => {}, (async (url, options) => {
    if (options?.method === 'POST') return Response.json({ data: { id: '300' } });
    if (String(url).includes('/mentions?')) return Response.json({ data: [mention('3', '@zectipbot help'), mention('2', '@zectipbot help')] });
    return Response.json({ data: { id: '100', username: 'zectipbot' } });
  }) as typeof fetch);
  try { await runBot(c, s, api, true); assert.equal(s.get('since_id'), '2'); assert.equal(s.attempted('3'), false); }
  finally { s.close(); }
});
test('nonexistent recipient gets a clear response, while credit and rate failures stop processing', async () => {
  const s = new State(':memory:'); const c = config();
  try {
    const m = mention('12', '@zectipbot request @missing 0.01 ZEC');
    const plan = await planMention(m, c, s, { lookup: async () => { throw new XError(404); } });
    assert.match(plan?.text ?? '', /not found/);
    await assert.rejects(planMention(m, c, s, { lookup: async () => { throw new XError(402); } }), /credits/);
  } finally { s.close(); }
});

test('budget refusal before POST preserves registration and releases only the definitely-unsent reply reservation', async () => {
  const s = new State(':memory:'); s.set('since_id', '1');
  const c = config(); let blockWrite = true, posts = 0;
  const api = () => new XApi(c, cost => {
    if (cost === 200 && blockWrite) throw new Error('Local daily X spending guard reached.');
  }, (async (url, options) => {
    if (options?.method === 'POST') { posts++; return Response.json({ data: { id: '999' } }); }
    if (String(url).includes('/mentions?')) return Response.json({ data: [mention('2', `@zectipbot register ${address} public`, '400')] });
    return Response.json({ data: { id: '100', username: 'zectipbot' } });
  }) as typeof fetch);
  try {
    await assert.rejects(runBot(c, s, api(), true), /spending guard/);
    assert.equal(posts, 0); assert.equal(s.user('400')?.address, address);
    assert.equal(s.attempted('2'), false); assert.deepEqual(s.status().unresolved, []);
    assert.equal(s.get('since_id'), '1');
    blockWrite = false;
    await runBot(c, s, api(), true);
    assert.equal(posts, 1); assert.equal(s.attempted('2'), true); assert.equal(s.get('since_id'), '2');
    assert.deepEqual(s.status().unresolved, []);
  } finally { s.close(); }
});
test('unsent-reservation release cannot erase unknown or successful reply history', () => {
  const s = new State(':memory:');
  try {
    s.reserveReply('1', 10); s.uncertain('1');
    s.reserveReply('2', 10); s.finishReply('2', '500');
    s.reserveReply('3', 10);
    s.cancelUnsentReply('1'); s.cancelUnsentReply('2'); s.cancelUnsentReply('3');
    assert.equal(s.attempted('1'), true); assert.equal(s.attempted('2'), true); assert.equal(s.attempted('3'), false);
  } finally { s.close(); }
});

test('command mistakes receive targeted help without creating a payment or revealing input', async () => {
  const s=new State(':memory:'); const c=config(); let lookups=0;
  try{
    const p=await planMention(mention('15','@zectipbot request @alice 1e8 ZEC'),c,s,{lookup:async()=>{lookups++;throw new Error();}});
    assert.match(p?.text??'',/Use: @zectipbot request/); assert.equal(p?.payment,undefined); assert.equal(lookups,0);
    assert.equal(parseCommand('@zectipbot tip @alice 0.01 ZEC')?.type,'invalid');
    s.register('200',address);
    const remove=await planMention(mention('16','@zectipbot unregister'),c,s,{lookup:async()=>{throw new Error();}});
    remove?.apply?.(); assert.equal(s.user('200')?.address,null); assert.equal(s.user('200')?.opted_out,0);
    assert.ok(await planMention(mention('17','@zectipbot help'),c,s,{lookup:async()=>{throw new Error();}}));
  }finally{s.close();}
});

test('a later stop suppresses older queued commands and an older start cannot override the opt-out',async()=>{
 const s=new State(':memory:');s.set('since_id','1');s.register('200',address);
 let writes=0; const c=config();
 const api=new XApi(c,()=>{},(async(url,options)=>{
  if(options?.method==='POST'){writes++;return Response.json({data:{id:'999'}});}
  if(String(url).includes('/mentions?'))return Response.json({data:[mention('4','@zectipbot stop'),mention('3','@zectipbot help'),mention('2','@zectipbot start')]});
  return Response.json({data:{id:'100',username:'zectipbot'}});
 })as typeof fetch);
 try{await runBot(c,s,api,true);assert.equal(writes,0);assert.equal(s.user('200')?.opted_out,1);assert.equal(s.user('200')?.address,null);assert.equal(s.get('since_id'),'4');}
 finally{s.close();}
});
