import { test } from 'node:test';
import assert from 'node:assert/strict';
import { inflateSync } from 'node:zlib';
import QRCode from 'qrcode';
import { paymentCard, paymentPageUrl, cardAlt, CARD_WIDTH, CARD_HEIGHT } from '../src/cards.js';
import { loadConfig } from '../src/config.js';
import { State, BudgetLimitError } from '../src/state.js';
import { XApi, XRequestNotSentError } from '../src/x.js';
import { parseCommand, planMention, runBot } from '../src/bot.js';
import { paymentServer } from '../src/web.js';
const address = 'ztestsapling10yy2ex5dcqkclhc7z7yrnjq2z6feyjad56ptwlfgmy77dmaqqrl9gyhprdx59qgmsnyfska2kez';
const base = 'https://tips.example.org';
const payment = () => ({id:'a'.repeat(32),address,amount:'0.001',network:'testnet' as const,recipient:'alice',source:'2',expires:Date.now()+3_600_000});
const config = () => loadConfig({X_API_KEY:'dummy',X_API_SECRET:'dummy',X_ACCESS_TOKEN:'dummy',X_ACCESS_SECRET:'dummy',X_BOT_USER_ID:'100',X_POSTING_ENABLED:'true',PUBLIC_BASE_URL:base,ZCASH_NETWORK:'testnet'});
const mention = (text:string) => ({id:'2',author_id:'200',text,entities:{mentions:[{id:'100',username:'zectipbot'}]}});
const me = () => Response.json({data:{id:'100',username:'zectipbot'}});
const image = paymentCard(payment(),base);
function rgb(png:Buffer) {
  assert.deepEqual([...png.subarray(0,8)],[137,80,78,71,13,10,26,10]);
  assert.equal(png.readUInt32BE(16),CARD_WIDTH);assert.equal(png.readUInt32BE(20),CARD_HEIGHT);
  const parts:Buffer[]=[];
  for(let off=8;off<png.length;) { const n=png.readUInt32BE(off);if(png.toString('ascii',off+4,off+8)==='IDAT')parts.push(png.subarray(off+8,off+8+n));off+=n+12; }
  const rows=inflateSync(Buffer.concat(parts));assert.equal(rows.length,(CARD_WIDTH*3+1)*CARD_HEIGHT);
  return (x:number,y:number)=>[...rows.subarray(y*(CARD_WIDTH*3+1)+1+x*3,y*(CARD_WIDTH*3+1)+4+x*3)];
}

test('rendered PNG preserves every QR module for the exact HTTPS request, with a four-module quiet zone',()=>{
 const p=payment();const png=paymentCard(p,base);assert.ok(png.length<5_000_000);
 const pixel=rgb(png);const qr=QRCode.create(paymentPageUrl(p,base),{errorCorrectionLevel:'M'});
 const n=qr.modules.size,unit=Math.floor(310/(n+8)),left=796+Math.floor((326-n*unit)/2),top=141+Math.floor((326-n*unit)/2);
 for(let y=0;y<n;y++)for(let x=0;x<n;x++)assert.deepEqual(pixel(left+x*unit+Math.floor(unit/2),top+y*unit+Math.floor(unit/2)),qr.modules.get(y,x)?[28,43,34]:[255,255,255]);
 for(let d=1;d<=4*unit;d++)assert.deepEqual(pixel(left-d,top+Math.floor(n*unit/2)),[255,255,255]);
 assert.match(cardAlt(p),/TESTNET, no real value/);assert.match(cardAlt(p),/Payment unverified/);
});

test('cards reject expired requests, unsafe URLs, invalid destinations, malformed recipients and IDs',()=>{
 for(const p of [{...payment(),expires:1},{...payment(),address:'private-key'},{...payment(),recipient:'<script>'},{...payment(),id:'../.env'}])assert.throws(()=>paymentCard(p,base));
 for(const url of ['http://localhost:3001','https://user:secret@example.org','https://example.org/path','https://example.org/?x=1'])assert.throws(()=>paymentCard(payment(),url));
});

test('request is explicit; old tip commands give guidance without a lookup, transfer, or payment creation',async()=>{
 const state=new State(':memory:');let lookups=0;
 try {
  assert.deepEqual(parseCommand('@zectipbot request @Alice 0.001 ZEC'),{type:'request',recipient:'alice',amount:'0.001'});
  const plan=await planMention(mention('@zectipbot tip @alice 0.001 ZEC'),config(),state,{lookup:async()=>{lookups++;throw new Error();}});
  assert.match(plan?.text??'',/Automatic tipping is not enabled/);assert.match(plan?.text??'',/No funds were sent/);
  assert.equal(plan?.payment,undefined);assert.equal(lookups,0);
 } finally {state.close();}
});

test('media reply verifies before upload and again immediately before posting, with exact media bytes',async()=>{
 const calls:{url:string;init?:RequestInit}[]=[];const costs:number[]=[];
 const api=new XApi(config(),n=>{costs.push(n);},(async(url,init)=>{
  const u=String(url);calls.push({url:u,init});
  if(u.endsWith('/media/upload'))return Response.json({data:{id:'1234567890123456789'}});
  if(u.endsWith('/tweets'))return Response.json({data:{id:'999'}});
  return me();
 })as typeof fetch);
 assert.equal(await api.reply('2','Request: 0.001 ZEC for alice. Payment unverified.',image),'999');
 assert.deepEqual(calls.map(c=>c.url),['https://api.x.com/2/users/me','https://api.x.com/2/media/upload','https://api.x.com/2/users/me','https://api.x.com/2/tweets']);
 const body=JSON.parse(String(calls[1]?.init?.body));assert.equal(body.media_category,'tweet_image');
 assert.deepEqual(Buffer.from(body.media,'base64'),image);assert.match(String((calls[1]?.init?.headers as Record<string,string>).Authorization),/^OAuth /);
 assert.deepEqual(JSON.parse(String(calls[3]?.init?.body)).media,{media_ids:['1234567890123456789']});assert.deepEqual(costs,[10,100,10,200]);
});

test('upload access rejection falls back to one text reply, and avoids repeated uploads in the same worker process',async()=>{
 const c=config();let uploads=0,posts=0;
 const fetcher=(async(url,init)=>{
  if(String(url).endsWith('/media/upload')){uploads++;return new Response('NEVER_LOG_ME',{status:403});}
  if(init?.method==='POST'){posts++;assert.equal(JSON.parse(String(init.body)).media,undefined);return Response.json({data:{id:'999'}});}
  return me();
 })as typeof fetch;
 await new XApi(c,()=>{},fetcher).reply('2','Request link',image);
 await new XApi(c,()=>{},fetcher).reply('3','Request link',image);
 assert.equal(uploads,1);assert.equal(posts,2);
});

test('identity change after upload stops before the reply; upload/network failures release only unsent reservations',async()=>{
 let identity=0,posts=0;
 const api=new XApi(config(),()=>{},(async(url)=>{
  if(String(url).endsWith('/media/upload'))return Response.json({data:{id:'123'}});
  if(String(url).endsWith('/tweets')){posts++;return Response.json({data:{id:'999'}});}
  return Response.json({data:{id:++identity===1?'100':'777',username:'zectipbot'}});
 })as typeof fetch);
 await assert.rejects(api.reply('2','Request link',image),XRequestNotSentError);assert.equal(posts,0);
 for(const mode of ['network','budget','credits','rate','bad-id','pending']) {
  const s=new State(':memory:');s.set('since_id','1');s.register('400',address);let actualPosts=0;
  const c=config();const fetcher=(async(url)=>{
   const u=String(url);
   if(u.includes('/mentions?'))return Response.json({data:[mention('@zectipbot request @alice 0.001 ZEC')]});
   if(u.includes('/by/username/'))return Response.json({data:{id:'400',username:'alice'}});
   if(u.endsWith('/media/upload')){
    if(mode==='network')throw new Error('secret-network');
    if(mode==='credits'||mode==='rate')return new Response('secret-response',{status:mode==='credits'?402:429});
    return Response.json({data:mode==='bad-id'?{id:123}:{id:'123',processing_info:{state:'pending'}}});
   }
   if(u.endsWith('/tweets')){actualPosts++;return Response.json({data:{id:'999'}});}
   return me();
  })as typeof fetch;
  try {
   const api=new XApi(c,n=>{if(n===100&&mode==='budget')throw new BudgetLimitError('guard');},fetcher);
   await assert.rejects(runBot(c,s,api,true),XRequestNotSentError);
   assert.equal(actualPosts,0);assert.equal(s.attempted('2'),false);assert.equal(s.get('since_id'),'1');
  }finally{s.close();}
 }
});

test('uncertain post after successful upload is never repeated; preview makes no media calls',async()=>{
 const c=config(),s=new State(':memory:');s.set('since_id','1');s.register('400',address);let uploads=0,posts=0;
 const api=()=>new XApi(c,()=>{},(async(url)=>{
  const u=String(url);
  if(u.includes('/mentions?'))return Response.json({data:[mention('@zectipbot request @alice 0.001 ZEC')]});
  if(u.includes('/by/username/'))return Response.json({data:{id:'400',username:'alice'}});
  if(u.endsWith('/media/upload')){uploads++;return Response.json({data:{id:'123'}});}
  if(u.endsWith('/tweets')){posts++;throw new Error('uncertain');}
  return me();
 })as typeof fetch);
 try {
  await runBot(c,s,api(),false);assert.equal(uploads,0);assert.equal(posts,0);assert.equal(s.db.prepare('SELECT COUNT(*) AS n FROM payments').get()?.n,0);
  await assert.rejects(runBot(c,s,api(),true),/network/);await runBot(c,s,api(),true);
  assert.equal(uploads,1);assert.equal(posts,1);assert.equal(s.attempted('2'),true);assert.equal(s.status().unresolved[0]?.status,'unknown');
 }finally{s.close();}
});

test('public card route, metadata and download use configured origin, not Host; expiry applies to cached images and HEAD',async()=>{
 const s=new State(':memory:');const p=s.createPayment(payment());const server=paymentServer(config(),s);
 const get=(url:string,method='GET')=>new Promise<{status:number;headers:Record<string,string>;body:Buffer}>(resolve=>{
  let status=200;const headers:Record<string,string>={};
  const response={setHeader(k:string,v:string){headers[k.toLowerCase()]=v;return this;},writeHead(v:number){status=v;return this;},end(body=''){resolve({status,headers,body:Buffer.isBuffer(body)?body:Buffer.from(body)});}};
  server.emit('request',{url,method,headers:{host:'attacker.invalid'}},response);
 });
 try {
  const html=(await get('/p/'+p.id)).body.toString();assert.match(html,/summary_large_image/);assert.ok(html.includes(base+'/p/'+p.id+'/card.png'));assert.ok(!html.includes('attacker.invalid'));
  assert.match(html,/Save payment card/);assert.match(html,/Payment unverified/);
  const route='/p/'+p.id+'/card.png';const card=await get(route);assert.equal(card.status,200);assert.equal(card.headers['content-type'],'image/png');rgb(card.body);
  assert.equal((await get(route,'HEAD')).body.length,0);
  s.db.prepare('UPDATE payments SET expires=1 WHERE id=?').run(p.id);
  assert.equal((await get(route)).status,410);assert.equal((await get(route,'HEAD')).status,410);
  assert.equal((await get('/p/'+'b'.repeat(32)+'/card.png')).status,404);
 }finally{s.close();}
});
