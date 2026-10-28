import { BOT_USERNAME, type Config, requirePublic } from './config.js';
import { State, type Payment } from './state.js';
import { XApi, XError, XRequestNotSentError, type Mention, type XUser } from './x.js';
import { paymentUri, zatoshis, amountString, validateAddress } from './zcash.js';
import { paymentCard } from './cards.js';
import { privateMention } from './private-tips.js';
import { TipStore } from './tip-store.js';
export type Command = { type: 'register'; address: string } | { type: 'request'; recipient: string; amount: string } | { type: 'help' | 'stop' | 'start' | 'unregister' } | { type: 'invalid'; usage: string };
export function parseCommand(text: string): Command | undefined {
  // An exact, explicit command to the bot is required. Prose and quoted commands are ignored.
  const m = /^@zectipbot\s+(.+)$/i.exec(text.trim());
  if (!m || text.length > 2400) return;
  const body = m[1]!;
  if (/^(help|stop|start|unregister)$/i.test(body)) return { type: body.toLowerCase() as 'help' | 'stop' | 'start' | 'unregister' };
  const register = /^register\s+([a-zA-Z0-9]+)\s+public$/i.exec(body);
  if (register) return { type: 'register', address: register[1]! };
  const request = /^request\s+@([A-Za-z0-9_]{1,15})\s+(\d+(?:\.\d{1,8})?)\s+ZEC$/i.exec(body);
  if (request) return { type: 'request', recipient: request[1]!.toLowerCase(), amount: request[2]! };
  if (/^tip\b/i.test(body)) return { type: 'invalid', usage: 'Automatic tipping is not enabled. No funds were sent. To create a wallet payment request, use: @zectipbot request @recipient 0.001 ZEC. You approve and pay in your wallet.' };
  if (/^request\b/i.test(body)) return { type: 'invalid', usage: 'Use: @zectipbot request @recipient 0.001 ZEC. Include ZEC and no extra text. This creates a wallet payment request; no funds move automatically.' };
  if (/^register\b/i.test(body)) return { type: 'invalid', usage: 'Use: @zectipbot register YOUR_RECEIVING_ADDRESS public. Replace the placeholder with your own receiving address. The word public acknowledges that your address and X association will be public.' };
  if (/^(help|stop|start|unregister)\b/i.test(body)) return { type: 'invalid', usage: 'Send one command with no extra text: @zectipbot help | stop | start | unregister. Stop disables replies and removes your address; unregister removes only the address.' };
  return;
}
const HELP = 'Create a wallet request: request @user 0.001 ZEC. Register: register ADDRESS public (publishes your address). Remove address: unregister. Stop and remove: stop. Resume: start. Bot fee: 0; wallet network fees apply. No funds move automatically.';
type Plan = { privateAction?: boolean; text?: string; payment?: Omit<Payment, 'id'>; apply?: () => void };
export async function planMention(m: Mention, c: Config, state: State, api: Pick<XApi, 'lookup'> & Partial<Pick<XApi, 'postAuthor'>>): Promise<Plan | undefined> {
  if (!/^\d+$/.test(m.id) || !/^\d+$/.test(m.author_id) || m.author_id === c.botId) return;
  // X mention entities bind the command to the real bot account when supplied.
  const botEntity = m.entities?.mentions?.find(e => e.username.toLowerCase() === BOT_USERNAME);
  if (!botEntity || botEntity.id !== c.botId) return;
  if (c.privateTips) return privateMention(m,c,state,api);
  const cmd = parseCommand(m.text);
  if (!cmd) return;
  if (cmd.type === 'stop') return { apply: () => state.opt(m.author_id, true) };
  if (cmd.type === 'start') return { text: 'Replies enabled. Send @zectipbot help for commands. Post @zectipbot stop to opt out again.', apply: () => state.opt(m.author_id, false) };
  if (state.user(m.author_id)?.opted_out) return;
  if (cmd.type === 'help') return { text: HELP };
  if (cmd.type === 'invalid') return { text: cmd.usage };
  if (cmd.type === 'unregister') return { text: 'Receiving address removed. Replies remain enabled. Previously shared payment links cannot be revoked. Send stop to disable replies too.', apply: () => state.unregister(m.author_id) };
  if (cmd.type === 'register') {
    try {
      const address = validateAddress(cmd.address, c.network).address;
      return {
        text: `Public receiving address registered for your X account on ${c.network}. Bot fee: 0; wallet network fees apply. Requests do not confirm payment. Post @zectipbot stop to opt out and remove your registration.`,
        apply: () => state.register(m.author_id, address)
      };
    } catch {
      return { text: `Address not registered. Use a valid ${c.network} Unified, Sapling, or transparent receiving address with: register ADDRESS public. Never share a seed or key.` };
    }
  }
  if (cmd.type !== 'request') return;
  let amount: string;
  try {
    const n = zatoshis(cmd.amount);
    if (n > zatoshis(c.maxTip)) throw new Error();
    amount = amountString(n);
  } catch { return { text: 'Invalid amount or tip limit exceeded. Use a positive ZEC amount with at most 8 decimal places. No USD conversion is performed.' }; }
  let recipient: XUser;
  try { recipient = await api.lookup(cmd.recipient); }
  catch (error) {
    if (error instanceof XError && error.status === 404) return { text: 'Recipient X account was not found. Check the username and try a new command. No funds were sent. Post @zectipbot stop to opt out.' };
    throw error;
  }
  const user = state.user(recipient.id);
  if (!user?.address || user.opted_out) return { text: 'This recipient has no active public receiving address registered. Ask them to register their own address. No funds were sent. Post @zectipbot stop to opt out.' };
  // Validate again under current network/config; never accept a sender-supplied destination override.
  paymentUri({ address: user.address, amount, network: c.network, max: c.maxTip });
  return {
    payment: { address: user.address, amount, network: c.network, recipient: cmd.recipient, expires: Date.now() + c.ttlHours * 3_600_000, source: m.id }
  };
}
export function paymentReply(p: Payment, baseUrl: string): string {
  return `Request: ${p.amount} ZEC for ${p.recipient}. Review and pay in your wallet: ${baseUrl}/p/${p.id} Bot fee: 0; wallet network fees apply. Payment unverified. Opt out: @zectipbot stop`;
}
export const currentSnowflake = (now = Date.now()): string => ((BigInt(now) - 1288834974657n) << 22n).toString();
export async function initialize(api: XApi, state: State) {
  await api.me();
  if (state.get('since_id')) throw new Error('Mention cursor is already initialized. It was preserved.');
  // Fresh cursor skips historical mentions and avoids sending unsolicited old replies.
  state.set('since_id', currentSnowflake());
  console.log('Mention cursor starts now. New explicit commands can be processed on the next run.');
}
export async function runBot(c: Config, state: State, api: XApi, live: boolean, options: { sessionUser?: XUser; quiet?: boolean } = {}) {
  if (live && !c.posting) throw new Error('Set X_POSTING_ENABLED=true locally only when ready, then use npm run bot:live.');
  const me = options.sessionUser ?? await api.me();
  if (me.id !== c.botId || me.username.toLowerCase() !== BOT_USERNAME) throw new Error('Authenticated account is not the pinned @zectipbot account.');
  const since = state.get('since_id');
  if (!since) throw new Error('Initialize with npm run bot:init before reading new commands.');
  let page: string | undefined;
  const all: Mention[] = [];
  for (let i = 0; i < c.maxPages; i++) {
    const result = await api.mentions(me.id, since, page);
    if (result.errors?.length) throw new Error('X returned partial mention errors; cursor preserved.');
    if (result.data && (!Array.isArray(result.data) || result.data.some(m => !m || !/^\d+$/.test(m.id) || typeof m.text !== 'string' || typeof m.author_id !== 'string'))) throw new Error('Invalid mention response; cursor preserved.');
    all.push(...(result.data ?? []));
    page = result.meta?.next_token;
    if (!page) break;
  }
  if (page) throw new Error('Mention backlog exceeds MAX_MENTION_PAGES. No cursor was advanced. Increase the page limit carefully after checking costs.');
  const mentions = [...new Map(all.map(m => [m.id, m])).values()].sort((a, b) => BigInt(a.id) < BigInt(b.id) ? -1 : 1);
  // Honor a newer explicit opt-out before older queued interactions from that
  // author, even after a daily-budget pause. An older start cannot undo it.
  const latestStops = new Map<string, string>();
  for (const m of mentions) {
    const entity = m.entities?.mentions?.find(item => item.username.toLowerCase() === BOT_USERNAME && item.id === c.botId);
    if (BigInt(m.id) > BigInt(since) && /^\d+$/.test(m.author_id) && m.author_id !== c.botId && entity && parseCommand(m.text)?.type === 'stop') latestStops.set(m.author_id, m.id);
  }
  if (live) for (const author of latestStops.keys()) { state.opt(author, true); if(c.privateTips) new TipStore(state).disable(author,c.network); }
  let replies = 0, handled = 0;
  for (const m of mentions) {
    if (BigInt(m.id) <= BigInt(since)) continue;
    const laterStop = latestStops.get(m.author_id);
    if (laterStop && BigInt(m.id) < BigInt(laterStop)) {
      if (live) state.set('since_id', m.id);
      continue;
    }
    if (replies >= c.maxReplies) break; // remaining commands stay after the durable cursor
    if (!state.attempted(m.id)) {
      const plan = await planMention(m, c, state, api);
      if (plan) {
        let reply = plan.text;
        let card: Buffer | undefined;
        if (plan.payment) {
          if (live) requirePublic(c);
          const p = live ? state.createPayment(plan.payment) : { ...plan.payment, id: 'PREVIEW_ONLY' };
          reply = paymentReply(p, c.baseUrl);
          if (live && p.expires <= Date.now()) reply = 'This request expired before its reply could be sent. Post a fresh @zectipbot request command. No funds were sent.';
          else if (live && c.paymentCards) card = paymentCard(p, c.baseUrl);
        }
        if (reply && (reply.length > 280 || /[^\x20-\x7E]/.test(reply))) throw new Error('Reply would exceed the supported X length. Shorten PUBLIC_BASE_URL.');
        if (live) {
          // Durable reservation precedes any write. Uncertain writes are never retried automatically.
          if (reply) state.reserveReply(m.id, c.maxDailyReplies);
          plan.apply?.();
          if (reply) {
            try { const id = await api.reply(m.id, reply, card); state.finishReply(m.id, id); }
            catch (error) {
              if (error instanceof XRequestNotSentError) state.cancelUnsentReply(m.id);
              else state.uncertain(m.id);
              throw error;
            }
          }
        } else {
          console.log(JSON.stringify({ sourcePost: m.id, action: c.privateTips && plan.privateAction ? 'private_action' : parseCommand(m.text)?.type, reply: reply ?? (c.privateTips ? '(no public reply; private status only)' : '(no reply; local opt-out change only)'), paymentPage: plan.payment ? 'preview only; no payment created' : undefined, card: plan.payment && c.paymentCards ? 'PNG card planned; no render, upload, or X write during preview' : undefined }));
        }
        if (reply) replies++;
        handled++;
      }
    }
    if (live) state.set('since_id', m.id);
  }
  if (!options.quiet || handled > 0) console.log(`${live ? 'Live run' : 'Preview'} finished: ${handled} command(s), ${replies} ${live ? 'reply attempt(s)' : 'draft reply/replies'}. ${live ? 'X API calls consume credits.' : 'Preview does not change registrations, payment requests, reply records, or the mention cursor. X reads still cost credits.'}`);
}



