import { createServer, type IncomingMessage } from 'node:http';
import { randomBytes } from 'node:crypto';
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import type { AddressInfo } from 'node:net';
import { BOT_USERNAME, type Config, requireApp } from './config.js';
import { oauthHeader, type Credentials } from './oauth.js';
import { XApi } from './x.js';
import type { State } from './state.js';
export async function tokenRequest(path: 'request_token' | 'access_token', c: Credentials, extra: Record<string, string>, fetcher: typeof fetch = fetch): Promise<URLSearchParams> {
  const url = `https://api.x.com/oauth/${path}`;
  let response: Response;
  try { response = await fetcher(url, { method: 'POST', headers: { Authorization: oauthHeader('POST', url, c, extra) }, redirect: 'error', signal: AbortSignal.timeout(15_000) }); }
  catch { throw new Error('OAuth network request failed. Restart authorization.'); }
  if (!response.ok) {
    // Only validated numeric error codes may leave this function. Upstream error
    // messages and bodies can echo credentials, so never log or render them.
    let codes: number[] = [];
    try {
      const body: unknown = await response.json();
      if (body && typeof body === 'object' && 'errors' in body && Array.isArray(body.errors)) {
        codes = [...new Set(body.errors.flatMap((entry: unknown) =>
          entry && typeof entry === 'object' && 'code' in entry &&
          typeof entry.code === 'number' && Number.isInteger(entry.code) && entry.code >= 0 && entry.code <= 9999
            ? [entry.code] : []))].slice(0, 6);
      }
    } catch { /* Non-JSON bodies are deliberately discarded. */ }
    const diagnostic = codes.length ? `; X error code(s): ${codes.join(', ')}` : '; X error code not provided';
    throw new Error(`OAuth ${path} failed (HTTP ${response.status}${diagnostic}). Check callback URL and existing app permissions. No credentials were saved.`);
  }
  const fields = new URLSearchParams(await response.text());
  if (!fields.get('oauth_token') || !fields.get('oauth_token_secret') || (path === 'request_token' && fields.get('oauth_callback_confirmed') !== 'true')) throw new Error('OAuth response was incomplete or callback was not confirmed.');
  return fields;
}
export function saveCredentials(values: Record<string, string>, path = '.env') {
  let text = readFileSync(path, 'utf8');
  for (const [key, value] of Object.entries(values)) {
    if (!/^X_[A-Z_]+$/.test(key) || /[\r\n"\\]/.test(value)) throw new Error('Invalid credential encoding.');
    const line = `${key}="${value}"`;
    const re = new RegExp(`^${key}=.*$`, 'gm');
    text = re.test(text) ? text.replace(re, () => line) : `${text.trimEnd()}\n${line}\n`;
  }
  // Same-directory atomic replace. Request-token credentials exist only in RAM.
  const temp = `${path}.${randomBytes(8).toString('hex')}.tmp`;
  writeFileSync(temp, text, { mode: 0o600, flag: 'wx' });
  renameSync(temp, path);
}
export function validCallback(req: IncomingMessage, expected: string, pending: { token: string; session: string; expires: number } | undefined, now = Date.now()): boolean {
  const u = new URL(req.url ?? '/', expected);
  const cookies = (req.headers.cookie ?? '').split(';').map(s => s.trim());
  return Boolean(pending && pending.expires > now && cookies.includes(`zec_oauth=${pending.session}`) && u.searchParams.getAll('oauth_token').length === 1 && u.searchParams.get('oauth_token') === pending.token && u.searchParams.getAll('oauth_verifier').length === 1 && /^[A-Za-z0-9_-]{1,256}$/.test(u.searchParams.get('oauth_verifier') ?? ''));
}
export function createAuthorizationServer(c: Config, state: State, fetcher: typeof fetch = fetch) {
  requireApp(c);
  const callback = new URL(c.callback);
  if (callback.protocol !== 'http:' || callback.hostname !== '127.0.0.1' || callback.pathname !== '/callback' || callback.search || callback.hash || callback.username || callback.password || !callback.port) throw new Error('Use an explicit loopback callback such as http://127.0.0.1:3000/callback.');
  const csrf = randomBytes(24).toString('hex');
  let pending: { token: string; secret: string; session: string; expires: number } | undefined;
  let busy = false;
  const server = createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store'); res.setHeader('Referrer-Policy', 'same-origin');
    res.setHeader('Content-Security-Policy', "default-src 'none'; form-action 'self' https://api.x.com https://x.com https://api.twitter.com https://twitter.com; frame-ancestors 'none'");
    if (req.headers.host !== callback.host) { res.writeHead(400).end('Invalid host.'); return; }
    const url = new URL(req.url ?? '/', callback.origin);
    try {
      if (req.method === 'GET' && url.pathname === '/') {
        res.setHeader('Content-Type', 'text/html; charset=utf-8');
        res.end(`<h1>Authorize @${BOT_USERNAME}</h1><p>Use the existing app owned by @farad_0. Sign in to X as @zectipbot. A different account will be rejected. This step will not post.</p><form method="post" action="/start?csrf=${csrf}"><button>Continue to X authorization</button></form>`);
      } else if (req.method === 'GET' && url.pathname === '/start') {
        res.writeHead(303, { Location: '/' }).end();
      } else if (req.method === 'POST' && url.pathname === '/start') {
        if (url.searchParams.getAll('csrf').length !== 1 || url.searchParams.get('csrf') !== csrf || req.headers.origin !== callback.origin) {
          res.writeHead(403).end('This authorization page is stale or could not be validated. Open the local home page again and use its button.'); return;
        }
        if (busy || pending) { res.writeHead(409).end('Authorization is already in progress. Restart the command to try again.'); return; }
        busy = true;
        try {
          const tokens = await tokenRequest('request_token', { key: c.apiKey, secret: c.apiSecret }, { oauth_callback: c.callback }, fetcher);
          const session = randomBytes(24).toString('hex');
          pending = { token: tokens.get('oauth_token')!, secret: tokens.get('oauth_token_secret')!, session, expires: Date.now() + 600_000 };
          res.setHeader('Set-Cookie', `zec_oauth=${session}; HttpOnly; SameSite=Lax; Path=/; Max-Age=600`);
          res.writeHead(302, { Location: `https://api.x.com/oauth/authorize?oauth_token=${encodeURIComponent(pending.token)}` }).end();
        } finally { busy = false; }
      } else if (req.method === 'GET' && url.pathname === '/callback') {
        if (!validCallback(req, callback.origin, pending)) { res.writeHead(400).end('Authorization expired, was denied, or could not be matched. Restart npm run auth.'); return; }
        const current = pending!; pending = undefined; // one use; reject callback replay
        const tokens = await tokenRequest('access_token', { key: c.apiKey, secret: c.apiSecret, token: current.token, tokenSecret: current.secret }, { oauth_verifier: url.searchParams.get('oauth_verifier')! }, fetcher);
        const access = { key: c.apiKey, secret: c.apiSecret, token: tokens.get('oauth_token')!, tokenSecret: tokens.get('oauth_token_secret')! };
        const api = new XApi(c, cost => state.reserveCost(cost, c.budgetMills), fetcher, access);
        const user = await api.me(false);
        saveCredentials({ X_ACCESS_TOKEN: access.token, X_ACCESS_SECRET: access.tokenSecret, X_BOT_USER_ID: user.id, X_POSTING_ENABLED: 'false' });
        console.log(`Verified @${user.username} (ID ${user.id}). Saved user credentials locally; posting remains disabled.`);
        res.setHeader('Set-Cookie', 'zec_oauth=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0');
        res.end('Verified @zectipbot. Credentials saved in your local .env. You can close this tab. Posting is disabled.');
        server.close();
      } else { res.writeHead(404).end('Not found.'); }
    } catch (error) {
      // All errors here are sanitized app errors; never print upstream responses or URLs.
      const message = error instanceof Error && /^(OAuth|Authenticated|Local daily|X )/.test(error.message) ? error.message : 'Authorization could not finish. Existing credentials were preserved. Restart the command.';
      console.error(message); res.writeHead(400).end(message);
    }
  });
  return server;
}
export async function authorize(c: Config, state: State) {
  const server = createAuthorizationServer(c, state);
  const port = Number(new URL(c.callback).port);
  await new Promise<void>((resolve, reject) => { server.once('error', () => reject(new Error('Could not start the local authorization server. Check the callback port.'))); server.listen(port, '127.0.0.1', resolve); });
  console.log(`Open http://127.0.0.1:${(server.address() as AddressInfo).port} in your browser and authorize @zectipbot. No credentials will be printed.`);
  const timer = setTimeout(() => { server.close(); console.log('Authorization window expired. Run npm run auth again.'); }, 10 * 60_000);
  server.once('close', () => clearTimeout(timer));
  return server;
}
