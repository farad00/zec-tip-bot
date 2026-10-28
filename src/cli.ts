import { join } from 'node:path';
import { loadConfig, requireUser } from './config.js';
import { State } from './state.js';
import { XApi } from './x.js';
import { authorize } from './auth.js';
import { initialize, runBot } from './bot.js';
import { paymentServer, listen } from './web.js';
import { watch } from './worker.js';
import { watchWallet } from './wallet-worker.js';
async function main() {
  const command = process.argv[2];
  if (!['auth', 'verify', 'init', 'preview', 'live', 'serve', 'demo', 'status', 'watch', 'start', 'resume'].includes(command ?? '')) throw new Error('Use npm run auth, verify, bot:init, bot:preview, bot:live, bot:watch, start, serve, demo, or status.');
  const c = loadConfig();
  const state = new State(join(c.dataDir, 'bot.sqlite'));
  let keepOpen = false;
  try {
    if (command === 'auth') { const server = await authorize(c, state); keepOpen = true; server.once('close', () => state.close()); return; }
    if (command === 'resume') {
      state.acquire('bot');
      try { state.set('worker_hold', ''); } finally { state.release('bot'); }
      console.log('Automatic-worker hold cleared. No X call or post was made. Restart the worker explicitly after fixing the fault.'); return;
    }
    if (command === 'status') { console.log(JSON.stringify(state.status(), null, 2)); return; }
    if (command === 'serve' || command === 'demo') {
      if (command === 'demo') {
        // ZIP-321 specification's testnet sample. NEVER fund this public example address.
        const sample = 'ztestsapling10yy2ex5dcqkclhc7z7yrnjq2z6feyjad56ptwlfgmy77dmaqqrl9gyhprdx59qgmsnyfska2kez';
        const p = state.createPayment({ address: sample, amount: '0.01', network: 'testnet', recipient: 'DEMO_DO_NOT_FUND', expires: Date.now() + 3_600_000, source: `demo-${Date.now()}` });
        console.log(`Offline TESTNET demo. No X calls or real recipient. Do not fund the sample address. Open http://127.0.0.1:${c.webPort}/p/${p.id}`);
      }
      const server = paymentServer(c, state); await listen(server, c.webHost, c.webPort);
      if(c.hostedWallets){
        const controller=new AbortController();
        const running=watchWallet(c,state,controller.signal);
        let closing=false;
        let stop!:()=>void;
        try{await new Promise<void>((resolve,reject)=>{
          stop=()=>{if(closing)return;closing=true;controller.abort();server.close(error=>error?reject(error):resolve());};
          process.once('SIGINT',stop);process.once('SIGTERM',stop);
          running.catch(()=>{console.error('Private wallet worker stopped. Transfers require attention.');stop();});
          console.log('Private account pages and Testnet wallet processing are running.');
        });}finally{controller.abort();await running;process.removeListener('SIGINT',stop);process.removeListener('SIGTERM',stop);}
        return;
      }
      keepOpen = true; server.once('close', () => state.close());
      console.log(`Payment pages are listening on ${c.webHost}:${c.webPort}. Public links use ${c.baseUrl}. Keep this running while requests are in use.`);
      return;
    }
    if (command === 'watch' || command === 'start') {
      const hostedServer = command === 'start' ? paymentServer(c, state) : undefined;
      if (hostedServer) {
        await listen(hostedServer, c.webHost, c.webPort);
        if (state.get('worker_hold')) {
          keepOpen = true; hostedServer.once('close', () => state.close());
          console.log('Automatic worker is held after a fatal error. Payment pages remain available. Inspect status, fix the fault, use bot:resume, then restart the service.');
          return;
        }
        console.log('Payment server and automatic mention worker are starting together.');
      }
      const controller = new AbortController();
      const stop = () => controller.abort();
      process.once('SIGINT', stop); process.once('SIGTERM', stop);
      const walletRun=c.hostedWallets?watchWallet(c,state,controller.signal):undefined;
      try { await watch(c, state, { signal: controller.signal }); }
      finally {
        controller.abort();await walletRun;
        process.removeListener('SIGINT', stop); process.removeListener('SIGTERM', stop);
        if (hostedServer) await new Promise<void>(resolve => hostedServer.close(() => resolve()));
      }
      return;
    }
    requireUser(c);
    const api = new XApi(c, cost => state.reserveRequest(cost, c.budgetMills));
    if (command === 'verify') { const me = await api.me(); console.log(`Verified @${me.username}, pinned ID ${me.id}. Posting enabled: ${c.posting}. No post was made.`); return; }
    state.acquire('bot');
    try {
      if (command === 'init') await initialize(api, state);
      else await runBot(c, state, api, command === 'live');
    } finally { state.release('bot'); }
  } finally { if (!keepOpen) state.close(); }
}
main().catch(error => {
  // Never dump stacks, request headers, upstream bodies, .env contents, or raw mention text.
  const known = error instanceof Error && /^(Use |Invalid |Enter |Run |Set |Private |Live |PUBLIC_|ZCASH_|X_|X |OAuth|Authenticated|Local |Per-run|Daily |Mention |Initialize|A bot|Reply |Recipient |Address |Amount |Could not|Use an|Authorization|Unsupported|Automatic)/.test(error.message);
  console.error(known ? error.message : 'Command failed. Check local configuration and service access. Credentials and raw errors were not printed.');
  process.exitCode = 1;
});

