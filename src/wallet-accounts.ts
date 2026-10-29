import {randomBytes} from 'node:crypto';
import {TipStore} from './tip-store.js';
import {validateAddress} from './zcash.js';
export type WalletAddressIndex=0|4294967296;
export type AccountWalletHealth={network:'testnet';synced:boolean;currentConsensusVerified:boolean;backupConfirmed:boolean;accountCreationReady:boolean;depositsEnabled:boolean;transfersEnabled:boolean;accountAdmissionRequired?:boolean;receivingIndex?:WalletAddressIndex};
export type WalletBalance={network:'testnet';account:string;confirmedSpendableZat:number;minimumConfirmations:10;transparentBalancePresent:boolean};
export type WalletReceipt={receipt:string;amountZat:number;confirmations:number;time:number|null};
export interface AccountWalletBackend {
 health():Promise<AccountWalletHealth>;
 accounts():Promise<{account:string;name:string}[]>;
 createAccount(name:string):Promise<{account:string}>;
 admitAccount?(binding:{account:string;userId:string;label:string;address:string}):Promise<void>;
 welcomeStatus?(account:string):Promise<{state:'queued'|'submitted'|'failed'|'review'|'not_available';amountZat:number}>;
 // Only fixed, explicit shielded receiver indices are allowed. Stored receiving
 // addresses are immutable; an index-policy update applies to new setups only.
 shieldedAddress(account:string,index:WalletAddressIndex):Promise<{network:'testnet';account:string;address:string}>;
 balance?(account:string):Promise<WalletBalance>;
 receipts?(account:string):Promise<{network:'testnet';account:string;receipts:WalletReceipt[]}>;
}
type Provision={user_id:string;label:string;phase:'creating'|'deriving'|'ready'|'review';account:string|null;reason:string|null};
const uuid=(v:string)=>/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(v);
export class WalletSetupRequired extends Error {}
export class WalletProvisionReview extends Error {}
export class WalletAccountProvisioner {
 constructor(readonly store:TipStore,private wallet:AccountWalletBackend) {
  store.state.db.exec(`CREATE TABLE IF NOT EXISTS wallet_provisions (
   user_id TEXT PRIMARY KEY,label TEXT NOT NULL UNIQUE,phase TEXT NOT NULL,account TEXT UNIQUE,reason TEXT
  );CREATE TABLE IF NOT EXISTS wallet_account_leases(name TEXT PRIMARY KEY,owner TEXT NOT NULL,expires INTEGER NOT NULL);`);
 }
 private row(id:string){return this.store.state.db.prepare('SELECT * FROM wallet_provisions WHERE user_id=?').get(id) as Provision|undefined;}
 private review(id:string,reason:string):never {
  this.store.state.db.prepare("UPDATE wallet_provisions SET phase='review',reason=? WHERE user_id=?").run(reason,id);
  throw new WalletProvisionReview('Wallet setup needs reconciliation. No deposit address was enabled.');
 }
 async provision(id:string) {
  if(!/^\d{1,20}$/.test(id)||!this.store.profile(id,'testnet'))throw new WalletSetupRequired('A verified X account is required.');
  const owner=randomBytes(24).toString('hex'),now=Date.now(),db=this.store.state.db;
  const locked=db.prepare("INSERT INTO wallet_account_leases VALUES ('provision',?,?) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires=excluded.expires WHERE wallet_account_leases.expires<?").run(owner,now+180_000,now);
  if(!locked.changes)throw new WalletSetupRequired('Wallet setup is busy.');
  const fence=()=>{if(!(db.prepare("UPDATE wallet_account_leases SET expires=? WHERE name='provision' AND owner=? AND expires>?").run(Date.now()+180_000,owner,Date.now()).changes))throw new WalletSetupRequired('Wallet setup lease expired.');};
  try {
   const health=await this.wallet.health();fence();
   if(health.accountAdmissionRequired&&!this.wallet.admitAccount)throw new WalletSetupRequired('Wallet recovery admission is required.');
   if(health.network!=='testnet'||(!health.accountCreationReady&&!this.row(id))||!health.currentConsensusVerified||!health.backupConfirmed)throw new WalletSetupRequired('Wallet synchronization, consensus and recovery must be verified first.');
   const existing=this.store.wallet(id,'testnet');if(existing)return existing;
   let row=this.row(id),fresh=false;
   if(row?.phase==='review')throw new WalletProvisionReview('Wallet setup needs reconciliation.');
   if(!row){
    // Random opaque label avoids putting the numeric X ID or username in the native wallet.
    db.prepare("INSERT INTO wallet_provisions VALUES (?,?,'creating',NULL,NULL)").run(id,'tip-'+randomBytes(16).toString('hex'));row=this.row(id)!;fresh=true;
   }
   if(row.phase==='creating') {
    const accounts=await this.wallet.accounts();fence();
    const matched=accounts.filter(account=>account.name===row!.label);
    if(matched.length>1)return this.review(id,'duplicate_native_label');
    let account:string;
    if(matched.length===1)account=matched[0]!.account;
    else if(!fresh)return this.review(id,'uncertain_account_creation');
    else {
     // The durable creating row is committed BEFORE the native mutation. Never blindly repeat it.
     try {account=(await this.wallet.createAccount(row.label)).account;fence();}
     catch {throw new WalletProvisionReview('Account creation result is uncertain. Retry only to reconcile its existing label.');}
    }
    if(!uuid(account))return this.review(id,'invalid_native_account');
    const other=db.prepare('SELECT user_id FROM wallet_provisions WHERE account=? AND user_id!=?').get(account,id);
    if(other)return this.review(id,'account_already_bound');
    db.prepare("UPDATE wallet_provisions SET account=?,phase='deriving' WHERE user_id=? AND phase='creating'").run(account,id);
    row=this.row(id)!;
   }
   if(row.phase==='deriving') {
    if(!row.account||!uuid(row.account))return this.review(id,'invalid_native_account');
    // Repeating derivation after interruption is safe only at the SAME index and receiver set.
    const derived=await this.wallet.shieldedAddress(row.account,health.receivingIndex??0);fence();
    if(derived.network!=='testnet'||derived.account!==row.account)return this.review(id,'address_account_mismatch');
    try {
     const checked=validateAddress(derived.address,'testnet');
     if(checked.transparentReceiver)return this.review(id,'transparent_receiver');
     // Validation and admission are separate: an unavailable backup operation must
     // leave derivation resumable, rather than label a valid address as invalid.
    }catch(error){if(error instanceof WalletProvisionReview)throw error;return this.review(id,'invalid_or_reused_address');}
    if(health.accountAdmissionRequired){await this.wallet.admitAccount!({account:row.account,userId:id,label:row.label,address:derived.address});fence();}
    try {
     db.exec('BEGIN IMMEDIATE');
     try {
      this.store.attachVerifiedTestnetWallet(id,row.account,derived.address);
      db.prepare("UPDATE wallet_provisions SET phase='ready' WHERE user_id=? AND phase='deriving'").run(id);
      db.exec('COMMIT');
     }catch(error){db.exec('ROLLBACK');throw error;}
    }catch(error){if(error instanceof WalletProvisionReview)throw error;return this.review(id,'invalid_or_reused_address');}
   }
   const result=this.store.wallet(id,'testnet');if(!result)throw new WalletProvisionReview('Wallet setup is not complete.');
   // Provisioning never opts the user into automatic spending. Operator-funded
   // welcome grants have their own fixed budget and authorization in the broker.
   return result;
  }finally{db.prepare("DELETE FROM wallet_account_leases WHERE name='provision' AND owner=?").run(owner);}
 }
}
