import {setTimeout as wait} from 'node:timers/promises';
import type {Config} from './config.js';
import type {State} from './state.js';
import {TipStore} from './tip-store.js';
import {TipEngine,type TipWalletBackend} from './tip-engine.js';
import {WalletTransferClient} from './wallet-transfer-client.js';
export async function processWalletBatch(state:State,wallet:TipWalletBackend,signal:AbortSignal){
 const engine=new TipEngine(new TipStore(state),wallet);
 // Never use the X cursor to decide whether a previously queued payment exists.
 const rows=state.db.prepare("SELECT source FROM tip_jobs WHERE network='testnet' AND status IN ('queued','preparing','prepared','signing','signed','broadcasting','submitted','confirmed') ORDER BY CASE WHEN status='confirmed' THEN 1 ELSE 0 END,created DESC LIMIT 20").all() as {source:string}[];
 for(const row of rows){if(signal.aborted)return;await engine.process(row.source);}
}
export async function watchWallet(c:Config,state:State,signal:AbortSignal,log:(message:string)=>void=console.log){
 if(!c.privateTips||!c.walletAccounts||!c.hostedWallets||c.network!=='testnet')throw new Error('Private Testnet wallet processing is not enabled.');
 const wallet=new WalletTransferClient(c.walletBrokerSocket);let wasHeld=false;
 log('Private Testnet wallet processing started. X access and public replies are handled separately.');
 while(!signal.aborted){
  try{await processWalletBatch(state,wallet,signal);if(wasHeld)log('Private wallet processing resumed.');wasHeld=false;}
  catch{if(!wasHeld)log('Private wallet processing is waiting for backend readiness. Uncertain payments remain held.');wasHeld=true;}
  if(signal.aborted)break;
  try{await wait(15_000,undefined,{signal});}catch(error){if(!signal.aborted)throw error;}
 }
}
