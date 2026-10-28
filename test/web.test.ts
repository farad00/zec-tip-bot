import { test } from 'node:test';
import assert from 'node:assert/strict';
import { State } from '../src/state.js';
import { loadConfig } from '../src/config.js';
import { paymentServer, listen } from '../src/web.js';
import type { AddressInfo } from 'node:net';
const address = 'ztestsapling10yy2ex5dcqkclhc7z7yrnjq2z6feyjad56ptwlfgmy77dmaqqrl9gyhprdx59qgmsnyfska2kez';
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
