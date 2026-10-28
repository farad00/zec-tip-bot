import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Script } from 'node:vm';
import { paymentServer } from '../src/web.js';
import { State } from '../src/state.js';
import { loadConfig } from '../src/config.js';
const address='ztestsapling10yy2ex5dcqkclhc7z7yrnjq2z6feyjad56ptwlfgmy77dmaqqrl9gyhprdx59qgmsnyfska2kez';
function fixture(){
 const state=new State(':memory:');
 const server=paymentServer(loadConfig({}),state);
 const get=(url:string,method='GET')=>new Promise<{status:number;headers:Record<string,string>;body:string}>(resolve=>{
  let status=200; const headers:Record<string,string>={};
  const response={setHeader(k:string,v:string){headers[k.toLowerCase()]=v;return this;},writeHead(n:number){status=n;return this;},end(body=''){resolve({status,headers,body});}};
  server.emit('request',{url,method},response);
 });
 return {state,get};
}
test('redesigned checkout keeps the exact payment URI, escaped recipient, full destination and real QR data',async()=>{
 const f=fixture();
 try{
  const p=f.state.createPayment({address,amount:'0.001',network:'testnet',recipient:'<script>bad</script>',expires:Date.now()+3600000,source:'demo'});
  const r=await f.get('/p/'+p.id);
  assert.equal(r.status,200); assert.ok(r.body.includes('zcash:'+address+'?amount=0.001'));
  assert.ok(r.body.includes('&lt;script&gt;bad&lt;/script&gt;'));
  assert.ok(!r.body.includes('<script>bad</script>'));
  assert.match(r.body,/data:image\/png;base64,/); assert.match(r.body,/Payment unverified/);
  assert.match(r.body,/No funds have been sent or confirmed/);
  assert.match(r.body,/data-copy-target="receiving-address"/); assert.match(r.body,/data-wallet-link/);
  assert.match(r.body,/Network fee/); assert.match(r.body,/0 ZEC/);
  assert.match(r.headers['content-security-policy']??'',/script-src 'self'/);
 }finally{f.state.close();}
});
test('home command builder is a client-side composer, and private project files are not served',async()=>{
 const f=fixture();
 try{
  const home=await f.get('/');
  assert.equal(home.status,200); assert.match(home.body,/Who made your day/);
  assert.match(home.body,/@zectipbot request @recipient 0.001 ZEC/);
  assert.match(home.body,/unregister/); assert.match(home.body,/data-theme-toggle/);
  assert.ok(home.body.includes('@media(max-width:720px)'));
  const script=await f.get('/assets/app.js');
  assert.match(script.headers['content-type']??'',/javascript/); new Script(script.body);
  assert.match(script.body,/clipboard.writeText/); assert.match(script.body,/https:\/\/x.com\/intent\/post/);
  assert.ok(!script.body.includes('fetch(')); assert.ok(!script.body.includes('process.env'));
  assert.equal((await f.get('/.env')).status,404);
  assert.equal((await f.get('/data/bot.sqlite')).status,404);
  assert.equal((await f.get('/','POST')).status,405);
 }finally{f.state.close();}
});
test('expired links cannot present an active wallet handoff and missing links have a useful recovery route',async()=>{
 const f=fixture();
 try{
  const p=f.state.createPayment({address,amount:'0.001',network:'testnet',recipient:'demo',expires:1,source:'expired'});
  const expired=await f.get('/p/'+p.id); assert.equal(expired.status,410);
  assert.match(expired.body,/This request has expired/); assert.ok(!expired.body.includes('data-wallet-link'));
  const missing=await f.get('/p/'+'a'.repeat(32)); assert.equal(missing.status,404);
  assert.match(missing.body,/Back to the command guide/);
 }finally{f.state.close();}
});
