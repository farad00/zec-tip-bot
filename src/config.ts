import {WALLET_HEALTH_FILE} from './wallet-health.js';
import {WALLET_ACCOUNT_SOCKET} from './wallet-account-client.js';
export const BOT_USERNAME = 'zectipbot';
export type Config = ReturnType<typeof loadConfig>;
function integer(env: NodeJS.ProcessEnv, key: string, fallback: number, min: number, max: number): number {
  const raw = env[key] ?? String(fallback);
  const value = Number(raw);
  if (!/^\d+$/.test(raw) || !Number.isSafeInteger(value) || value < min || value > max) throw new Error(`Invalid ${key}.`);
  return value;
}
export function loadConfig(env: NodeJS.ProcessEnv = process.env) {
  const network = env.ZCASH_NETWORK ?? 'mainnet';
  if (network !== 'mainnet' && network !== 'testnet') throw new Error('ZCASH_NETWORK must be mainnet or testnet.');
  const base = new URL(env.PUBLIC_BASE_URL ?? 'http://127.0.0.1:3001');
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.pathname !== '/' || base.search || base.hash) throw new Error('PUBLIC_BASE_URL must be an HTTP(S) origin.');
  const budget = env.DAILY_X_BUDGET_USD ?? '1.00';
  if (!/^\d+(\.\d{1,3})?$/.test(budget) || Number(budget) <= 0 || Number(budget) > 100) throw new Error('Invalid DAILY_X_BUDGET_USD (0 < budget <= 100).');
  if (env.DATA_DIR !== undefined && (!env.DATA_DIR.trim() || /[\0\r\n]/.test(env.DATA_DIR))) throw new Error('Invalid DATA_DIR.');
  if (env.PRIVATE_TIPPING_ENABLED === 'true' && network !== 'testnet') throw new Error('Private tipping currently requires ZCASH_NETWORK=testnet.');
  if(env.WALLET_HEALTH_FILE && (env.WALLET_HEALTH_FILE!==WALLET_HEALTH_FILE||network!=='testnet'))throw new Error('Wallet health observations require the fixed Testnet health file.');
  const walletAccounts=env.WALLET_ACCOUNTS_ENABLED==='true';
  if(walletAccounts&&(network!=='testnet'||env.PRIVATE_TIPPING_ENABLED!=='true'||env.WALLET_BROKER_SOCKET!==WALLET_ACCOUNT_SOCKET))throw new Error('Wallet account creation requires private Testnet mode and the fixed local broker socket.');
  if(env.HOSTED_WALLETS_ENABLED==='true'&&!walletAccounts)throw new Error('Private wallet processing requires the fixed Testnet account service.');
  return {
    network: network as 'mainnet' | 'testnet', baseUrl: base.origin, apiKey: env.X_API_KEY ?? '', apiSecret: env.X_API_SECRET ?? '',
    accessToken: env.X_ACCESS_TOKEN ?? '', accessSecret: env.X_ACCESS_SECRET ?? '',
    botId: env.X_BOT_USER_ID ?? '', callback: env.X_CALLBACK_URL ?? 'http://127.0.0.1:3000/callback',
    privateTips: env.PRIVATE_TIPPING_ENABLED === 'true', walletHealthFile:env.WALLET_HEALTH_FILE??'', userCallback: env.X_USER_CALLBACK_URL ?? new URL('/account/callback', base).href,
    walletAccounts,walletBrokerSocket:env.WALLET_BROKER_SOCKET??'',hostedWallets:env.HOSTED_WALLETS_ENABLED==='true',
    posting: env.X_POSTING_ENABLED === 'true', paymentCards: env.X_PAYMENT_CARDS !== 'false', webHost: env.WEB_HOST ?? '127.0.0.1',
    webPort: integer({ ...env, WEB_PORT: env.WEB_PORT ?? env.PORT }, 'WEB_PORT', 3001, 1024, 65535), dataDir: env.DATA_DIR ?? 'data', maxTip: env.MAX_TIP_ZEC ?? '1',
    ttlHours: integer(env, 'REQUEST_TTL_HOURS', 24, 1, 168),
    budgetMills: Math.floor(Number(budget) * 1000),
    maxReplies: integer(env, 'MAX_REPLIES_PER_RUN', 3, 1, 20),
    maxDailyReplies: integer(env, 'MAX_REPLIES_PER_DAY', 10, 1, 100),
    maxPages: integer(env, 'MAX_MENTION_PAGES', 3, 1, 10),
    maxCalls: integer(env, 'MAX_X_CALLS_PER_RUN', 30, 1, 100),
    pollSeconds: integer(env, 'POLL_INTERVAL_SECONDS', 60, 30, 3600)
  };
}
export function requireApp(c: Config) {
  if (!c.apiKey || !c.apiSecret) throw new Error('Enter the existing app API key and API key secret in your local .env first.');
}
export function requireUser(c: Config) {
  requireApp(c);
  if (!c.accessToken || !c.accessSecret || !/^\d+$/.test(c.botId)) throw new Error('Run npm run auth to authorize and pin @zectipbot first.');
}
export function requirePublic(c: Config) {
  const u = new URL(c.baseUrl);
  if (u.protocol !== 'https:' || u.hostname === 'localhost' || u.hostname.endsWith('.localhost') || /^[\d.]+$/.test(u.hostname) || u.hostname.includes(':')) {
    throw new Error('Live payment replies require a publicly reachable HTTPS hostname in PUBLIC_BASE_URL. Start the payment server and configure your hosting/tunnel first.');
  }
}

