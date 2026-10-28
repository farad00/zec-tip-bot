import { setTimeout as wait } from 'node:timers/promises';
import { type Config, requirePublic, requireUser } from './config.js';
import { BudgetLimitError, DailyReplyLimitError, State } from './state.js';
import { XApi, XError, type XUser } from './x.js';
import { initialize, runBot } from './bot.js';

export function workerDecision(error: unknown, failures: number, intervalMs: number, now = Date.now()) {
  const chain: unknown[] = [];
  let current = error;
  for (let i = 0; i < 5 && current; i++) {
    chain.push(current); current = current instanceof Error ? current.cause : undefined;
  }
  if (chain.some(e => e instanceof BudgetLimitError || e instanceof DailyReplyLimitError)) {
    const next = new Date(now); next.setUTCHours(24, 0, 5, 0);
    return { stop: false, delayMs: next.getTime() - now, message: 'Daily spending or reply limit reached. Pausing until the next UTC day.' };
  }
  const xError = chain.find(e => e instanceof XError) as XError | undefined;
  if (xError && [401, 402, 403].includes(xError.status)) return { stop: true, delayMs: 0, message: 'X access requires attention. Check authorization, app permissions, or credits before restarting the worker.' };
  if (xError?.status === 429) {
    const delay = xError.retryAtMs ? xError.retryAtMs - now + 1000 : 15 * 60_000;
    return { stop: false, delayMs: Math.max(intervalMs, Math.min(86_400_000, delay)), message: 'X rate limit reached. Pausing before the next mention check.' };
  }
  if (chain.some(e => e instanceof Error && /^(Authenticated|Per-run|Mention backlog|Invalid|Live payment)/.test(e.message)) || failures >= 3) {
    return { stop: true, delayMs: 0, message: 'Automatic checking stopped after an error. Inspect the local configuration and unresolved replies before restarting.' };
  }
  return { stop: false, delayMs: Math.min(15 * 60_000, intervalMs * 2 ** failures), message: 'A batch could not finish. Uncertain replies remain protected; mention checking will resume after a pause.' };
}

type Options = {
  signal: AbortSignal;
  log?: (message: string) => void;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  createApi?: () => XApi;
};
export async function watch(c: Config, state: State, options: Options) {
  if (state.get('worker_hold')) throw new Error('Automatic replies are held after a fatal error. Inspect status and fix access, then use bot:resume before restarting.');
  requireUser(c); requirePublic(c);
  if (!c.posting) throw new Error('Set X_POSTING_ENABLED=true before starting automatic replies.');
  const log = options.log ?? console.log;
  const sleep = options.sleep ?? (async (ms, signal) => { await wait(ms, undefined, { signal }); });
  const makeApi = options.createApi ?? (() => new XApi(c, cost => state.reserveRequest(cost, c.budgetMills), fetch, undefined, options.signal));
  const interval = c.pollSeconds * 1000;
  let sessionUser: XUser | undefined;
  let failures = 0;
  log('Automatic replies enabled. Checking explicit mentions every ' + c.pollSeconds + ' seconds. Each reply verifies @zectipbot again; daily limits still apply.');
  while (!options.signal.aborted) {
    let leased = false;
    let delay = interval;
    try {
      state.acquire('bot'); leased = true;
      const api = makeApi(); // fresh per-batch call limit; account identity cached only for this process
      if (!sessionUser) sessionUser = await api.me();
      if (!state.get('since_id')) await initialize(api, state);
      await runBot(c, state, api, true, { sessionUser, quiet: true });
      failures = 0;
    } catch (error) {
      if (options.signal.aborted) break;
      if (!leased) {
        log('Another bot command holds the worker lease. Waiting before checking again.');
        delay = Math.max(interval, 60_000);
      } else {
        failures++;
        const decision = workerDecision(error, failures, interval);
        log(decision.message);
        if (decision.stop) {
          state.set('worker_hold', new Date().toISOString()); // survives a hosting supervisor restart; avoids repeated paid startup calls
          throw new Error(decision.message);
        }
        delay = decision.delayMs;
      }
    } finally { if (leased) state.release('bot'); }
    if (options.signal.aborted) break;
    try { await sleep(delay, options.signal); } catch (error) { if (!options.signal.aborted) throw error; }
  }
  log('Automatic checking stopped cleanly.');
}
