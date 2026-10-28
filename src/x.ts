import { BOT_USERNAME, type Config, requireUser } from './config.js';
import { oauthHeader, type Credentials } from './oauth.js';
export type XUser = { id: string; username: string };
export type Mention = { id: string; author_id: string; text: string; created_at?: string; referenced_tweets?: { type: string; id: string }[]; entities?: { mentions?: { username: string; id?: string }[] } };
export type MentionPage = { data?: Mention[]; meta?: { newest_id?: string; next_token?: string }; errors?: unknown[] };
export class XError extends Error {
  constructor(readonly status: number, readonly retryAtMs?: number) {
    super(status === 429 ? 'X rate limit reached. Stop and wait for the limit to reset; no automatic retries.' :
      status === 402 ? 'X credits/access are insufficient. Check the existing developer app billing console.' :
      status === 401 || status === 403 ? 'X denied access. Check app Read and Write permissions, credits, and @zectipbot authorization.' :
      `X request failed (HTTP ${status}). No automatic retries.`);
  }
}
// This error is used only when the POST has definitely not been dispatched.
export class XRequestNotSentError extends Error {}
export type Fetcher = typeof fetch;
const unavailableMedia = new WeakSet<Config>();
export class XApi {
  private calls = 0;
  constructor(readonly config: Config, private reserve: (mills: number) => void | ((actual: number) => void) = () => {}, private fetcher: Fetcher = fetch, private credentials?: Credentials, private signal?: AbortSignal) {}
  private creds(): Credentials {
    return this.credentials ?? { key: this.config.apiKey, secret: this.config.apiSecret, token: this.config.accessToken, tokenSecret: this.config.accessSecret };
  }
  async request<T>(method: string, path: string, cost: number, body?: unknown, actualCost?: (payload: T) => number | undefined): Promise<T> {
    if (++this.calls > this.config.maxCalls) throw new XRequestNotSentError('Per-run X API call limit reached.');
    let settle: void | ((actual: number) => void);
    try { settle = this.reserve(cost); }
    catch (error) { throw new XRequestNotSentError(error instanceof Error ? error.message : 'Local spending reservation failed.', { cause: error }); }
    const url = `https://api.x.com${path}`;
    let response: Response;
    try {
      response = await this.fetcher(url, {
        method, headers: { Authorization: oauthHeader(method, url, this.creds()), ...(body ? { 'Content-Type': 'application/json' } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}), redirect: 'error', signal: this.signal ? AbortSignal.any([this.signal, AbortSignal.timeout(15_000)]) : AbortSignal.timeout(15_000)
      });
    } catch { throw new Error('X network request failed or timed out. A write may have succeeded; inspect npm run status and the account before retrying.'); }
    if (!response.ok) {
      const reset = response.headers.get('x-rate-limit-reset') ?? '';
      const after = response.headers.get('retry-after') ?? '';
      const retryAt = /^\d{10}$/.test(reset) ? Number(reset) * 1000 : /^\d{1,5}$/.test(after) ? Date.now() + Number(after) * 1000 : undefined;
      throw new XError(response.status, retryAt);
    }
    let payload: T;
    try { payload = await response.json() as T; } catch { throw new Error('X returned an unreadable response.'); }
    if (typeof settle === 'function' && actualCost) {
      const amount = actualCost(payload);
      if (amount !== undefined) settle(amount);
    }
    return payload;
  }
  async me(pin = true): Promise<XUser> {
    if (pin) requireUser(this.config);
    const result = await this.request<{ data?: XUser }>('GET', '/2/users/me', 10);
    const user = result.data;
    if (!user || !/^\d+$/.test(user.id) || typeof user.username !== 'string' || user.username.toLowerCase() !== BOT_USERNAME || (this.config.botId && user.id !== this.config.botId)) {
      throw new Error('Authenticated account is not the pinned @zectipbot account. No posting is permitted. Authorize @zectipbot again.');
    }
    return user;
  }
  mentions(id: string, since?: string, page?: string): Promise<MentionPage> {
    const q = new URLSearchParams({ max_results: '10', 'tweet.fields': 'author_id,created_at,entities,referenced_tweets' });
    if (since) q.set('since_id', since);
    if (page) q.set('pagination_token', page);
    return this.request<MentionPage>('GET', `/2/users/${id}/mentions?${q}`, 50, undefined, payload => {
      if (payload.errors?.length) return undefined;
      if (Array.isArray(payload.data) && payload.data.length <= 10) return payload.data.length * 5;
      if (!payload.data && 'meta' in payload && (payload.meta as { result_count?: number })?.result_count === 0) return 0;
      return undefined;
    });
  }
  async lookup(username: string): Promise<XUser> {
    if (!/^[A-Za-z0-9_]{1,15}$/.test(username)) throw new Error('Invalid X username.');
    const result = await this.request<{ data?: XUser }>('GET', `/2/users/by/username/${username}`, 10);
    if (!result.data || !/^\d+$/.test(result.data.id) || result.data.username.toLowerCase() !== username.toLowerCase()) throw new Error('Recipient account could not be resolved.');
    return result.data;
  }
  async postAuthor(postId: string): Promise<XUser> {
    if (!/^\d{1,20}$/.test(postId)) throw new Error('Invalid parent post.');
    const result = await this.request<{data?: {id:string;author_id:string}}>('GET', `/2/tweets/${postId}?tweet.fields=author_id`, 5);
    if (result.data?.id!==postId || !/^\d{1,20}$/.test(result.data.author_id)) throw new Error('Recipient post could not be resolved.');
    return {id:result.data.author_id,username:''};
  }
  async reply(source: string, text: string, card?: Buffer): Promise<string> {
    if (!this.config.posting) throw new XRequestNotSentError('Posting is disabled.');
    if (!/^\d+$/.test(source) || text.length > 280 || /[^\x20-\x7E]/.test(text)) throw new XRequestNotSentError('Invalid or oversized reply.');
    let mediaId: string | undefined;
    if (card && this.config.paymentCards && !unavailableMedia.has(this.config)) {
      let uploading = false;
      // An orphaned upload cannot have posted a reply. Every failure here is provably pre-reply.
      try {
        if (card.length > 5_000_000 || !card.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) throw new Error('Invalid payment card.');
        await this.me();
        uploading = true;
        const media = await this.request<{ data?: { id?: string; processing_info?: { state?: string } }; errors?: unknown[] }>(
          'POST', '/2/media/upload', 100, { media: card.toString('base64'), media_category: 'tweet_image' });
        const id = media.data?.id, processing = media.data?.processing_info?.state;
        if (typeof id !== 'string' || !/^[0-9]{1,19}$/.test(id) || media.errors?.length || (processing && processing !== 'succeeded')) throw new Error('Payment card upload is not ready.');
        mediaId = id;
      } catch (error) {
        if (uploading && error instanceof XError && [400, 403, 404, 415].includes(error.status)) {
          unavailableMedia.add(this.config);
          console.warn('X image uploads are unavailable for this authorization. Sending the payment link without an image; cards are paused for this process.');
        } else throw new XRequestNotSentError('Payment card preparation failed before any reply was posted. The original command remains pending.', { cause: error });
      }
    }
    // Verify again immediately before EVERY actual reply, after any image upload.
    try { await this.me(); }
    catch (error) { throw new XRequestNotSentError(error instanceof Error ? error.message : 'X identity verification failed before posting.', { cause: error }); }
    const result = await this.request<{ data?: { id: string }; errors?: unknown[] }>('POST', '/2/tweets', 200, {
      text, reply: { in_reply_to_tweet_id: source }, ...(mediaId ? { media: { media_ids: [mediaId] } } : {})
    });
    if (!result.data?.id || !/^\d+$/.test(result.data.id) || result.errors?.length) throw new Error('X write result is uncertain; inspect the account manually.');
    return result.data.id;
  }
}

