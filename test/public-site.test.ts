import {test} from 'node:test';
import assert from 'node:assert/strict';
import {request,type IncomingHttpHeaders} from 'node:http';
import type {AddressInfo} from 'node:net';
import {paymentServer,listen} from '../src/web.js';
import {loadConfig} from '../src/config.js';
import {State} from '../src/state.js';
import {TipStore} from '../src/tip-store.js';

test('public metadata and cached assets never include signed-in account data',async()=>{
 const state=new State(':memory:');
 const config=loadConfig({PRIVATE_TIPPING_ENABLED:'true',ZCASH_NETWORK:'testnet',PUBLIC_BASE_URL:'https://tips.example.org'});
 const store=new TipStore(state),login=store.login('200','private_user','testnet');
 const server=paymentServer(config,state);await listen(server,'127.0.0.1',0);
 const port=(server.address() as AddressInfo).port;
 const read=(path:string,cookie='',method='GET')=>new Promise<{status:number|undefined;headers:IncomingHttpHeaders;body:Buffer}>((resolve,reject)=>{
  const req=request({hostname:'127.0.0.1',port,path,method,headers:{host:'tips.example.org',cookie}},res=>{
   const chunks:Buffer[]=[];res.on('data',chunk=>chunks.push(Buffer.from(chunk)));
   res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,body:Buffer.concat(chunks)}));
  });req.on('error',reject);req.end();
 });
 try{
  const home=await read('/'),signedHome=await read('/','__Host-zec_account='+login.token),html=home.body.toString();
  assert.equal(home.status,200);assert.deepEqual(home.body,signedHome.body);
  assert.equal(home.headers['x-robots-tag'],'index, follow');assert.equal(home.headers['cache-control'],'no-store');
  assert.equal(home.headers['strict-transport-security'],'max-age=31536000; includeSubDomains');
  assert.match(html,/<link rel="canonical" href="https:\/\/tips.example.org\/">/);
  assert.match(html,/<meta name="description"/);assert.match(html,/twitter:card" content="summary_large_image/);
  assert.match(html,/https:\/\/tips.example.org\/assets\/site-card-v1.png/);
  assert.doesNotMatch(html,/private_user|name="csrf"|we hold tip balances/i);
  for(const text of ['@zectipbot tip','@zectipbot tip @recipient','@zectipbot help','@zectipbot stop','@zectipbot start','If the recipient has no wallet, no tip is sent.','How a tip moves'])assert.ok(html.includes(text));
  for(const cookie of ['', '__Host-zec_account='+login.token]){
   const account=await read('/account',cookie),body=account.body.toString();
   assert.equal(account.headers['cache-control'],'no-store');assert.equal(account.headers['x-robots-tag'],'noindex, nofollow');
   assert.doesNotMatch(body,/og:image|twitter:card|rel="canonical"/);
   assert.match(body,/<meta name="robots" content="noindex, nofollow">/);
  }
  for(const path of ['/favicon.svg?v=3','/assets/private.js?v=3','/assets/site-card-v1.png']){
   const asset=await read(path);
   assert.equal(asset.status,200);assert.equal(asset.headers['cache-control'],'public, max-age=31536000, immutable');
   assert.equal(asset.headers['set-cookie'],undefined);
   assert.ok(!asset.body.includes(Buffer.from('private_user')));
  }
  const card=await read('/assets/site-card-v1.png');
  assert.equal(card.headers['content-type'],'image/png');assert.equal(card.headers['x-robots-tag'],'index, follow');
  assert.deepEqual([...card.body.subarray(0,8)],[137,80,78,71,13,10,26,10]);
  assert.equal(card.body.readUInt32BE(16),1200);assert.equal(card.body.readUInt32BE(20),630);
  assert.equal((await read('/','','HEAD')).headers['x-robots-tag'],'index, follow');
  assert.equal((await read('/account','','HEAD')).headers['x-robots-tag'],'noindex, nofollow');
  assert.equal((await read('/assets/site-card-v1.png','','POST')).status,405);
  assert.equal(state.db.prepare('SELECT COUNT(*) AS n FROM tip_jobs').get()!.n,0);
 }finally{await new Promise<void>(resolve=>server.close(()=>resolve()));state.close();}
});

test('HTTP loopback development does not enable HSTS',async()=>{
 const state=new State(':memory:'),server=paymentServer(loadConfig({}),state);await listen(server,'127.0.0.1',0);
 try{const response=await fetch(`http://127.0.0.1:${(server.address() as AddressInfo).port}/healthz`);assert.equal(response.headers.get('strict-transport-security'),null);}
 finally{await new Promise<void>(resolve=>server.close(()=>resolve()));state.close();}
});
