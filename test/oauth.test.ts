import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { oauthHeader, pct } from '../src/oauth.js';
import { saveCredentials, validCallback, tokenRequest } from '../src/auth.js';
import type { IncomingMessage } from 'node:http';
test('OAuth signer matches the published HMAC-SHA1 worked example', () => {
  // Current X documentation's published example; these are invalid public sample keys.
  const credentials = { key: 'xvz1evFS4wEEPTGEFPHBog', secret: 'kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw', token: '370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb', tokenSecret: 'LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE' };
  const header = oauthHeader('POST', 'https://api.x.com/1.1/statuses/update.json?include_entities=true', credentials, {}, new URLSearchParams({ status: 'Hello Ladies + Gentlemen, a signed OAuth request!' }), { nonce: 'kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg', timestamp: '1318622958' });
  assert.ok(header.includes('oauth_signature="' + pct('Ls93hJiZbQ3akF3HF3x1Bz8/zU4=') + '"'));
});
test('RFC3986 encoding and request-token callback signing', () => {
  assert.equal(pct(" !*'()"), '%20%21%2A%27%28%29');
  const header = oauthHeader('POST', 'https://api.x.com/oauth/request_token', { key: 'app', secret: 'secret' }, { oauth_callback: 'http://127.0.0.1:3000/callback' });
  assert.match(header, /oauth_callback="http%3A%2F%2F127/);
  assert.ok(!header.includes('oauth_token='));
});
test('callback rejects mismatch, replay/absence, expired session, missing cookie, and duplicate params', () => {
  const pending = { token: 'request', session: 'session', expires: 2000 };
  const req = (url: string, cookie = 'zec_oauth=session') => ({ url, headers: { cookie } }) as IncomingMessage;
  const ok = '/callback?oauth_token=request&oauth_verifier=verifier';
  assert.equal(validCallback(req(ok), 'http://127.0.0.1:3000', pending, 1000), true);
  assert.equal(validCallback(req(ok), 'http://127.0.0.1:3000', undefined, 1000), false);
  assert.equal(validCallback(req(ok), 'http://127.0.0.1:3000', pending, 2000), false);
  assert.equal(validCallback(req(ok, ''), 'http://127.0.0.1:3000', pending, 1000), false);
  assert.equal(validCallback(req(ok + '&oauth_token=evil'), 'http://127.0.0.1:3000', pending, 1000), false);
  assert.equal(validCallback(req(ok.replace('request', 'evil')), 'http://127.0.0.1:3000', pending, 1000), false);
});
test('credential update preserves app keys and never persists request token fields', () => {
  const dir = mkdtempSync(join(process.cwd(), 'data-test-auth-'));
  try {
    const path = join(dir, '.env'); writeFileSync(path, '# local\nX_API_KEY=app\nX_ACCESS_TOKEN=old\nX_POSTING_ENABLED=true\n');
    saveCredentials({ X_ACCESS_TOKEN: 'new', X_ACCESS_SECRET: 'local', X_POSTING_ENABLED: 'false', X_BOT_USER_ID: '123' }, path);
    const text = readFileSync(path, 'utf8');
    assert.match(text, /X_API_KEY=app/); assert.match(text, /X_ACCESS_TOKEN="new"/); assert.match(text, /X_POSTING_ENABLED="false"/);
    assert.equal((text.match(/^X_ACCESS_TOKEN=/gm) ?? []).length, 1);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
test('request-token response must explicitly confirm callback; upstream errors do not leak', async () => {
  const mock = (body: string, status = 200) => (async () => new Response(body, { status })) as typeof fetch;
  await assert.rejects(tokenRequest('request_token', { key: 'k', secret: 's' }, {}, mock('oauth_token=a&oauth_token_secret=b')), /callback/);
  await assert.rejects(tokenRequest('access_token', { key: 'k', secret: 's' }, {}, mock('TOP_SECRET', 401)), e => e instanceof Error && !e.message.includes('TOP_SECRET'));
  const fields = await tokenRequest('request_token', { key: 'k', secret: 's' }, {}, mock('oauth_token=a&oauth_token_secret=b&oauth_callback_confirmed=true'));
  assert.equal(fields.get('oauth_token'), 'a');
});

async function authorizationFixture() {
  const { createAuthorizationServer } = await import('../src/auth.js');
  const { loadConfig } = await import('../src/config.js');
  const { State } = await import('../src/state.js');
  const state = new State(':memory:');
  let calls = 0;
  const server = createAuthorizationServer(loadConfig({ X_API_KEY: 'dummy', X_API_SECRET: 'dummy' }), state,
    (async () => { calls++; return new Response('oauth_token=dummy&oauth_token_secret=dummy&oauth_callback_confirmed=true'); }) as typeof fetch);
  const dispatch = (method: string, url: string, origin?: string) => new Promise<{ status: number; headers: Record<string, string>; body: string }>(resolve => {
    const headers: Record<string, string> = {};
    let status = 200;
    const res = {
      setHeader(k: string, v: string) { headers[k.toLowerCase()] = v; return this; },
      writeHead(n: number, values: Record<string, string> = {}) {
        status = n; for (const [k, v] of Object.entries(values)) headers[k.toLowerCase()] = v;
        return this;
      },
      end(body = '') { resolve({ status, headers, body }); }
    };
    server.emit('request', { method, url, headers: { host: '127.0.0.1:3000', ...(origin ? { origin } : {}) } }, res);
  });
  return { state, dispatch, calls: () => calls };
}
test('actual authorization form preserves browser origin and redirects a valid button submission to X', async () => {
  const f = await authorizationFixture();
  try {
    const landing = await f.dispatch('GET', '/');
    // HTML form submissions under no-referrer send Origin: null (Fetch standard).
    assert.equal(landing.headers['referrer-policy'], 'same-origin');
    const action = /action="([^"]+)"/.exec(landing.body)?.[1]; assert.ok(action);
    const result = await f.dispatch('POST', action, 'http://127.0.0.1:3000');
    assert.equal(result.status, 302);
    assert.equal(result.headers.location, 'https://api.x.com/oauth/authorize?oauth_token=dummy');
    assert.match(result.headers['set-cookie'] ?? '', /HttpOnly; SameSite=Lax/);
    assert.equal(f.calls(), 1);
    assert.equal((await f.dispatch('POST', action, 'http://127.0.0.1:3000')).status, 409);
  } finally { f.state.close(); }
});
test('stale start URLs recover; bad origins and CSRF return a useful error without contacting X', async () => {
  const f = await authorizationFixture();
  try {
    const landing = await f.dispatch('GET', '/');
    const action = /action="([^"]+)"/.exec(landing.body)?.[1]; assert.ok(action);
    for (const origin of [undefined, 'null', 'https://untrusted.example']) {
      const result = await f.dispatch('POST', action, origin);
      assert.equal(result.status, 403); assert.match(result.body, /stale|validated/);
    }
    assert.equal((await f.dispatch('POST', '/start?csrf=stale', 'http://127.0.0.1:3000')).status, 403);
    assert.equal((await f.dispatch('POST', action + '&csrf=duplicate', 'http://127.0.0.1:3000')).status, 403);
    const recovery = await f.dispatch('GET', action);
    assert.equal(recovery.status, 303); assert.equal(recovery.headers.location, '/'); assert.equal(f.calls(), 0);
  } finally { f.state.close(); }
});

test('OAuth failure diagnostics expose only numeric X codes, never upstream messages or credential fields', async () => {
  const payload = { errors: [
    { code: 415, message: 'SECRET_IN_UPSTREAM_MESSAGE' },
    { code: 'SECRET_AS_CODE' }, { code: -1 }, { code: 10000 }, { code: 415 }
  ], oauth_token: 'SECRET_TOKEN', oauth_token_secret: 'SECRET_TOKEN_SECRET' };
  await assert.rejects(tokenRequest('request_token', { key: 'dummy', secret: 'dummy' }, {},
    (async () => Response.json(payload, { status: 403 })) as typeof fetch),
    error => error instanceof Error &&
      error.message.includes('request_token failed (HTTP 403; X error code(s): 415)') &&
      !error.message.includes('SECRET') && !error.message.includes('10000'));
  await assert.rejects(tokenRequest('request_token', { key: 'dummy', secret: 'dummy' }, {},
    (async () => new Response('<html>SECRET_IN_HTML</html>', { status: 403 })) as typeof fetch),
    error => error instanceof Error && error.message.includes('X error code not provided') && !error.message.includes('SECRET'));
});

test('authorization CSP allows only the local form and X HTTPS authorization redirect hosts', async () => {
  const f = await authorizationFixture();
  try {
    const page = await f.dispatch('GET', '/');
    const csp = page.headers['content-security-policy']; assert.ok(csp);
    const form = csp.split(';').map(s => s.trim()).find(s => s.startsWith('form-action '));
    assert.equal(form, "form-action 'self' https://api.x.com https://x.com https://api.twitter.com https://twitter.com");
    assert.ok(csp.includes("default-src 'none'"));
    assert.ok(csp.includes("frame-ancestors 'none'"));
  } finally { f.state.close(); }
});
