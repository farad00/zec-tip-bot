import { test } from 'node:test';
import assert from 'node:assert/strict';
import { State } from '../src/state.js';
import { loadConfig } from '../src/config.js';
import { paymentServer, listen } from '../src/web.js';
import type { AddressInfo } from 'node:net';
import {request} from 'node:http';
const address = 'ztestsapling10yy2ex5dcqkclhc7z7yrnjq2z6feyjad56ptwlfgmy77dmaqqrl9gyhprdx59qgmsnyfska2kez';
test('private home and sign-in pages load the existing local favicon without authentication', async () => {
  const state = new State(':memory:');
  const config = loadConfig({PRIVATE_TIPPING_ENABLED:'true',ZCASH_NETWORK:'testnet',PUBLIC_BASE_URL:'http://127.0.0.1'});
  const server = paymentServer(config,state); await listen(server,'127.0.0.1',0);
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const read = (path:string,method='GET') => new Promise<{status:number|undefined;headers:import('node:http').IncomingHttpHeaders;body:string}>((resolve,reject)=>{
    const req=request(base+path,{method,headers:{host:'127.0.0.1'}},res=>{
      let body='';res.setEncoding('utf8');res.on('data',chunk=>body+=chunk);res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,body}));
    });req.on('error',reject);req.end();
  });
  try {
    let brandMark='',brandColour='';
    for (const path of ['/','/account']) {
      const response = await read(path);
      assert.equal(response.status,200);
      assert.match(response.body,/<link rel="icon" href="\/favicon.svg\?v=3" type="image\/svg\+xml">/);
      brandMark=response.body.match(/<span class="brand-emblem">([^<]+)<\/span>/)![1]!;
      brandColour=response.body.match(/--accent:(#[a-f0-9]{6})/)![1]!;
    }
    const icon = await read('/favicon.svg?v=3');
    assert.equal(icon.status,200); assert.equal(icon.headers['content-type'],'image/svg+xml');
    assert.match(icon.body,/^<svg[^>]+xmlns="http:\/\/www.w3.org\/2000\/svg"/);
    assert.equal(icon.body.match(/<text[^>]*>([^<]+)<\/text>/)![1],brandMark);
    assert.equal(icon.body.match(/<circle[^>]+fill="([^"]+)"/)![1],brandColour);
    assert.equal(icon.headers['set-cookie'],undefined);
    assert.equal((await read('/favicon.svg?v=3','HEAD')).status,200);
    assert.equal((await read('/favicon.svg?v=3','POST')).status,405);
  } finally { await new Promise<void>(resolve=>server.close(()=>resolve())); state.close(); }
});

test('real HTTP payment page provides QR, exact URI, escaping, costs, expiry and no tracking', async () => {
  const s = new State(':memory:'); const c = loadConfig({});
  const p = s.createPayment({ address, amount: '0.01', network: 'testnet', recipient: '<script>alert(1)</script>', expires: Date.now() + 60_000, source: '1' });
  const server = paymentServer(c, s); await listen(server, '127.0.0.1', 0);
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  try {
    const r = await fetch(`${base}/p/${p.id}`); const html = await r.text();
    assert.equal(r.status, 200); assert.match(html, /data:image\/png;base64,/); assert.match(html, /amount=0.01/);
    assert.match(html, /&lt;script&gt;/); assert.ok(!html.includes('<script>')); assert.match(html, /No funds have been sent or confirmed/);
    assert.match(html, /Bot service fee/); assert.match(r.headers.get('content-security-policy') ?? '', /default-src 'none'/);
    assert.equal(r.headers.get('referrer-policy'), 'no-referrer');
    assert.equal((await fetch(`${base}/p/not-found`)).status, 404);
    assert.equal((await fetch(`${base}/p/${p.id}`, { method: 'POST' })).status, 405);
    const expired = s.createPayment({ ...p, source: '2', expires: 1 });
    assert.equal((await fetch(`${base}/p/${expired.id}`)).status, 410);
    assert.equal((await fetch(`${base}/healthz`)).status, 200);
  } finally { await new Promise<void>(resolve => server.close(() => resolve())); s.close(); }
});
