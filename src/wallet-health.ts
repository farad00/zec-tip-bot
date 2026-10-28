import {open, constants} from 'node:fs/promises';
import {glyph} from './account-ui.js';
export const WALLET_HEALTH_FILE='/run/zec-wallet-health/status.json';
export type WalletHealth={version:1;observedAt:number;network:'testnet';nodeRpc:boolean;walletRpc:boolean;nodeHeight?:number;nodeProgress?:number;walletStatus:'available'|'timeout'|'unavailable';walletHeight?:number;walletNodeHeight?:number;walletLocked?:boolean};
const integer=(x:unknown):x is number=>typeof x==='number'&&Number.isSafeInteger(x)&&x>=0&&x<=0xffff_ffff;
// This read-only observation cannot provision accounts, authorize spending, or establish chain consensus.
export function validateWalletHealth(raw:unknown,now=Date.now()):WalletHealth {
 if(!raw||typeof raw!=='object'||Array.isArray(raw))throw new Error('Invalid wallet health observation.');
 const v=raw as Record<string,unknown>;
 const keys=['version','observedAt','network','nodeRpc','walletRpc','nodeHeight','nodeProgress','walletStatus','walletHeight','walletNodeHeight','walletLocked'];
 if(Object.keys(v).some(k=>!keys.includes(k))||v.version!==1||v.network!=='testnet'||typeof v.observedAt!=='number'||!Number.isSafeInteger(v.observedAt)||v.observedAt>now+30_000||now-v.observedAt>120_000||typeof v.nodeRpc!=='boolean'||typeof v.walletRpc!=='boolean'||!['available','timeout','unavailable'].includes(String(v.walletStatus)))throw new Error('Invalid or stale wallet health observation.');
 for(const key of ['nodeHeight','walletHeight','walletNodeHeight'])if(v[key]!==undefined&&!integer(v[key]))throw new Error('Invalid wallet height.');
 if(v.nodeProgress!==undefined&&(typeof v.nodeProgress!=='number'||!Number.isFinite(v.nodeProgress)||v.nodeProgress<0||v.nodeProgress>1))throw new Error('Invalid node progress.');
 if(v.walletLocked!==undefined&&typeof v.walletLocked!=='boolean')throw new Error('Invalid wallet status.');
 if((!v.nodeRpc&&(v.nodeHeight!==undefined||v.nodeProgress!==undefined))||(!v.walletRpc&&(v.walletHeight!==undefined||v.walletNodeHeight!==undefined||v.walletLocked!==undefined))||(v.walletStatus!=='available'&&(v.walletHeight!==undefined||v.walletNodeHeight!==undefined||v.walletLocked!==undefined)))throw new Error('Inconsistent wallet health observation.');
 return {...v} as WalletHealth;
}
export async function readWalletHealth(path:string,now=Date.now()):Promise<WalletHealth|undefined> {
 if(!path)return;
 if(path!==WALLET_HEALTH_FILE)throw new Error('Invalid wallet health file path.');
 let file:Awaited<ReturnType<typeof open>>|undefined;
 try {
  file=await open(path,constants.O_RDONLY|(constants.O_NOFOLLOW??0));const stat=await file.stat();
  if(!stat.isFile()||stat.size>4096)throw new Error('Invalid health file.');
  return validateWalletHealth(JSON.parse(await file.readFile('utf8')),now);
 }catch{return;}finally{await file?.close();}
}
export function walletHealthPanel(h:WalletHealth|undefined) {
 if(!h)return '<section class="ui-panel account-status"><div class="sync-visual"><div class="sync-ring"><span>'+glyph('nodes')+'</span></div><div class="sync-copy"><h2>Wallet service</h2><p>Live sync status is unavailable. Wallet readiness is checked separately by the account service.</p></div></div></section>';
 const height=(n:number)=>new Intl.NumberFormat('en-NG').format(n);
 const node=h.nodeRpc?'Testnet node: '+(h.nodeHeight===undefined?'connected':height(h.nodeHeight)+' blocks')+(h.nodeProgress===undefined?'':' · '+(h.nodeProgress*100).toFixed(1)+'% estimated progress'):'Waiting for the Testnet node connection.';
 const wallet=!h.walletRpc?'Waiting for the wallet connection.':h.walletStatus==='timeout'?'Wallet API connected. The scan-status query timed out while synchronization continues.':h.walletStatus!=='available'?'Wallet API connected. Scan status is temporarily unavailable.':'Wallet API connected.'+(h.walletHeight===undefined?'':' Scanned height: '+height(h.walletHeight)+'.')+(h.walletLocked===true?' Catching up; balance and spending remain locked.':'');
 const progress=h.nodeProgress===undefined?0:Math.min(100,Math.max(0,h.nodeProgress*100));
 return '<section class="composer account-status" aria-label="Wallet synchronization"><div class="sync-visual"><div class="sync-ring" style="--sync-progress:'+progress.toFixed(1)+'"><span>'+glyph('nodes')+'</span></div><div class="sync-copy"><h2>Wallet synchronization</h2><p>'+node+'</p><p>'+wallet+'</p></div></div><p class="hint">Observed chain progress is an estimate. Spending availability is verified separately; a connected service alone cannot authorize a transfer.</p></section>';
}
