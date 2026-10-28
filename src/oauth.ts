import { createHmac, randomBytes } from 'node:crypto';
export type Credentials = { key: string; secret: string; token?: string; tokenSecret?: string };
export const pct = (s: string): string => encodeURIComponent(s).replace(/[!'()*]/g, c => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
export function oauthHeader(method: string, url: string, c: Credentials, extra: Record<string, string> = {}, form?: URLSearchParams, fixed?: { nonce: string; timestamp: string }): string {
  const u = new URL(url);
  const oauth: Record<string, string> = {
    oauth_consumer_key: c.key, oauth_nonce: fixed?.nonce ?? randomBytes(24).toString('hex'),
    oauth_signature_method: 'HMAC-SHA1', oauth_timestamp: fixed?.timestamp ?? String(Math.floor(Date.now() / 1000)),
    oauth_version: '1.0', ...(c.token ? { oauth_token: c.token } : {}), ...extra
  };
  const pairs = [...u.searchParams, ...(form ? [...form] : []), ...Object.entries(oauth)]
    .map(([k, v]) => [pct(k), pct(v)] as const)
    .sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0);
  const normalized = pairs.map(([k, v]) => `${k}=${v}`).join('&');
  const base = [method.toUpperCase(), `${u.origin}${u.pathname}`, normalized].map(pct).join('&');
  oauth.oauth_signature = createHmac('sha1', `${pct(c.secret)}&${pct(c.tokenSecret ?? '')}`).update(base).digest('base64');
  return 'OAuth ' + Object.entries(oauth).sort().map(([k, v]) => `${pct(k)}="${pct(v)}"`).join(', ');
}
