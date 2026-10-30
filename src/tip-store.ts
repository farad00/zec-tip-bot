import { createHash, randomBytes } from 'node:crypto';
import type { State } from './state.js';
import { zatoshis, amountString, validateAddress } from './zcash.js';
export type TipNetwork = 'testnet' | 'mainnet';
export type TipProfile = { user_id: string; network: TipNetwork; username: string; default_zat: number | null; daily_zat: number | null; fee_zat: number | null; enabled: number; enabled_after: number; public_ack: number };
export type TipJob = { source: string; network: TipNetwork; sender: string; recipient: string; amount_zat: number; fee_zat: number; reserved_zat: number; day: string; status: string; reason: string | null; created: number };
const hash = (s: string) => createHash('sha256').update(s).digest('hex');
export const formatZat = (n: number) => amountString(BigInt(n));
function money(s: string, max: bigint) { const n = zatoshis(s); if (n > max) throw new Error('Invalid tipping limit.'); return Number(n); }
export class TipStore {
  constructor(readonly state: State) {
    state.db.exec(`
      CREATE TABLE IF NOT EXISTS tip_profiles (user_id TEXT NOT NULL, network TEXT NOT NULL, username TEXT NOT NULL, default_zat INTEGER, daily_zat INTEGER, fee_zat INTEGER, enabled INTEGER NOT NULL DEFAULT 0, enabled_after INTEGER NOT NULL DEFAULT 0, public_ack INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,network));
      CREATE TABLE IF NOT EXISTS tip_sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, network TEXT NOT NULL, csrf TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS tip_wallets (user_id TEXT NOT NULL, network TEXT NOT NULL, account TEXT NOT NULL, address TEXT NOT NULL, status TEXT NOT NULL, PRIMARY KEY(user_id,network), UNIQUE(network,account), UNIQUE(network,address));
      CREATE TABLE IF NOT EXISTS tip_jobs (source TEXT PRIMARY KEY, network TEXT NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL, amount_zat INTEGER NOT NULL, fee_zat INTEGER NOT NULL, reserved_zat INTEGER NOT NULL, day TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, created INTEGER NOT NULL);
      CREATE INDEX IF NOT EXISTS tip_jobs_sender ON tip_jobs(sender,network,day);
      CREATE TABLE IF NOT EXISTS tip_withdrawals(source TEXT PRIMARY KEY,address TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS tip_withdrawal_forms(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires INTEGER NOT NULL);
    `);
  }
  profile(id: string, network: TipNetwork) { return this.state.db.prepare('SELECT * FROM tip_profiles WHERE user_id=? AND network=?').get(id,network) as TipProfile | undefined; }
  login(id: string, username: string, network: TipNetwork, now = Date.now()) {
    if (!/^\d{1,20}$/.test(id) || !/^[A-Za-z0-9_]{1,15}$/.test(username)) throw new Error('Invalid account identity.');
    this.state.db.prepare('INSERT INTO tip_profiles(user_id,network,username) VALUES (?,?,?) ON CONFLICT(user_id,network) DO UPDATE SET username=excluded.username').run(id,network,username);
    const token=randomBytes(32).toString('hex'), csrf=randomBytes(24).toString('hex');
    this.state.db.prepare('DELETE FROM tip_sessions WHERE expires<=?').run(now);
    this.state.db.prepare('INSERT INTO tip_sessions VALUES (?,?,?,?,?,?)').run(hash(token),id,network,csrf,now,now+3_600_000);
    return {token,csrf};
  }
  session(token: string, network: TipNetwork, now=Date.now()) {
    if (!/^[a-f0-9]{64}$/.test(token)) return;
    return this.state.db.prepare('SELECT user_id,csrf,created FROM tip_sessions WHERE token_hash=? AND network=? AND expires>?').get(hash(token),network,now) as {user_id:string;csrf:string;created:number}|undefined;
  }
  logout(token: string) { this.state.db.prepare('DELETE FROM tip_sessions WHERE token_hash=?').run(hash(token)); }
  settings(id:string, network:TipNetwork, values:{amount:string;daily:string;fee:string;enabled:boolean;publicAck:boolean;consent:boolean}, max:string, now=Date.now()) {
    const defaultZat=money(values.amount,zatoshis(max)), dailyZat=money(values.daily,2_100_000_000_000_000n), feeZat=money(values.fee,zatoshis(max));
    if (defaultZat+feeZat>dailyZat || !values.consent || !this.profile(id,network)) throw new Error('Invalid tipping settings or consent.');
    // Save a preset before provisioning, but never enable spending without a ready wallet.
    const ready=this.wallet(id,network)?.status==='ready';
    if (values.enabled && (!ready || network!=='testnet')) throw new Error('Automatic tips need a verified Testnet wallet first.');
    this.state.db.prepare('UPDATE tip_profiles SET default_zat=?,daily_zat=?,fee_zat=?,enabled=?,enabled_after=?,public_ack=? WHERE user_id=? AND network=?').run(defaultZat,dailyZat,feeZat,values.enabled?1:0,now,values.publicAck?1:0,id,network);
  }
  disable(id:string,network:TipNetwork) { this.state.db.prepare('UPDATE tip_profiles SET enabled=0 WHERE user_id=? AND network=?').run(id,network); }
  attachVerifiedTestnetWallet(id:string,account:string,address:string) {
    if(!this.profile(id,'testnet') || !/^[A-Za-z0-9_-]{1,100}$/.test(account))throw new Error('Invalid wallet mapping.');
    const checked=validateAddress(address,'testnet');
    if(checked.transparentReceiver)throw new Error('A shielded-only receiving address is required.');
    const old=this.wallet(id,'testnet');
    if(old) {if(old.account!==account || old.address!==checked.address)throw new Error('Wallet mapping is immutable.');return;}
    this.state.db.prepare("INSERT INTO tip_wallets VALUES (?,'testnet',?,?,'ready')").run(id,account,checked.address);
  }
  wallet(id:string,network:TipNetwork) { return this.state.db.prepare('SELECT account,address,status FROM tip_wallets WHERE user_id=? AND network=?').get(id,network) as {account:string;address:string;status:string}|undefined; }
  job(source:string) { return this.state.db.prepare('SELECT * FROM tip_jobs WHERE source=?').get(source) as TipJob|undefined; }
  history(id:string,network:TipNetwork) {
    // Explicit fields and account filter: no endpoint can request another user's history.
    return this.state.db.prepare('SELECT source,sender,recipient,amount_zat,fee_zat,status,reason,created FROM tip_jobs WHERE network=? AND (sender=? OR (recipient=? AND status IN (\'submitted\',\'confirmed\'))) ORDER BY created DESC LIMIT 30').all(network,id,id) as TipJob[];
  }
  receiptIdsInHistory(id:string,network:TipNetwork) {
    if(!this.state.db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tip_spends'").get())return new Set<string>();
    const rows=this.state.db.prepare("SELECT s.txid FROM tip_spends s JOIN tip_jobs j ON j.source=s.source WHERE j.network=? AND j.recipient=? AND j.status IN ('submitted','confirmed') AND NOT EXISTS (SELECT 1 FROM tip_withdrawals w WHERE w.source=j.source)").all(network,id) as {txid:string|null}[];
    return new Set(rows.filter(row=>typeof row.txid==='string'&&/^[a-f0-9]{64}$/.test(row.txid)).map(row=>hash('zec-tip-bot:receipt:'+row.txid)));
  }
  withdrawal(source:string) {return this.state.db.prepare('SELECT address FROM tip_withdrawals WHERE source=?').get(source) as {address:string}|undefined;}
  withdrawalForm(id:string,now=Date.now()) {
    if(!this.profile(id,'testnet'))throw new Error('Invalid withdrawal account.');
    this.state.db.prepare('DELETE FROM tip_withdrawal_forms WHERE expires<=?').run(now);
    const token=randomBytes(16).toString('hex');
    this.state.db.prepare('INSERT INTO tip_withdrawal_forms VALUES (?,?,?)').run(token,id,now+600_000);
    return token;
  }
  enqueueWithdrawal(id:string,form:string,address:string,amount:string,fee:string,now=Date.now()):TipJob {
    const destination=validateAddress(address,'testnet');
    if(destination.kind!=='unified'||destination.transparentReceiver)throw new Error('A shielded-only Testnet Unified Address is required.');
    const amountZat=money(amount,1_000_000n),feeZat=money(fee,100_000n);
    if(!/^[a-f0-9]{32}$/.test(form)||this.wallet(id,'testnet')?.status!=='ready')throw new Error('Invalid withdrawal request.');
    const source='withdraw-'+form,db=this.state.db;
    db.exec('BEGIN IMMEDIATE');
    try {
      const formRow=db.prepare('SELECT user_id,expires FROM tip_withdrawal_forms WHERE id=?').get(form) as {user_id:string;expires:number}|undefined;
      if(!formRow||formRow.user_id!==id||formRow.expires<=now)throw new Error('Withdrawal form expired.');
      const old=this.job(source);
      if(old){
        if(old.sender!==id||old.amount_zat!==amountZat||old.fee_zat!==feeZat||this.withdrawal(source)?.address!==destination.address)throw new Error('Withdrawal form was already used for different terms.');
        db.exec('COMMIT');return old;
      }
      // A fresh, explicitly confirmed withdrawal is independent of automatic-tip
      // consent and opt-out. It never goes through an X mention or public reply.
      db.prepare("INSERT INTO tip_jobs VALUES (?,'testnet',?,?,?,?,?,?, 'queued',NULL,?)")
        .run(source,id,id,amountZat,feeZat,amountZat+feeZat,new Date(now).toISOString().slice(0,10),now);
      db.prepare('INSERT INTO tip_withdrawals VALUES (?,?)').run(source,destination.address);
      const result=this.job(source)!;db.exec('COMMIT');return result;
    }catch(error){db.exec('ROLLBACK');throw error;}
  }
  enqueue(source:string,sender:string,recipient:string,network:TipNetwork,commandTime:number,now=Date.now()):TipJob {
    if (!/^\d{1,20}$/.test(source) || !/^\d{1,20}$/.test(sender) || !/^\d{1,20}$/.test(recipient)) throw new Error('Invalid tip identity.');
    this.state.db.exec('BEGIN IMMEDIATE');
    try {
      const old=this.job(source); if(old) { this.state.db.exec('COMMIT'); return old; }
      const p=this.profile(sender,network), senderWallet=this.wallet(sender,network), recipientWallet=this.wallet(recipient,network);
      const day=new Date(now).toISOString().slice(0,10), amount=p?.default_zat??0, fee=p?.fee_zat??0;
      let reason:string|null=null;
      if(network!=='testnet') reason='mainnet_disabled';
      else if(sender===recipient) reason='self_tip';
      else if(!p?.enabled || !p.default_zat || !p.daily_zat || !p.fee_zat) reason='setup_required';
      else if(!Number.isFinite(commandTime) || commandTime<p.enabled_after || commandTime>now+60_000 || now-commandTime>900_000) reason='stale_command';
      else if(this.state.user(sender)?.opted_out) reason='tips_paused';
      else if(senderWallet?.status!=='ready' || recipientWallet?.status!=='ready') reason='wallet_unavailable';
      const used=(this.state.db.prepare("SELECT COALESCE(SUM(reserved_zat),0) AS n FROM tip_jobs WHERE sender=? AND network=? AND day=? AND status!='failed'").get(sender,network,day) as {n:number}).n;
      if(!reason && used+amount+fee>p!.daily_zat!) reason='daily_limit';
      this.state.db.prepare('INSERT INTO tip_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)').run(source,network,sender,recipient,amount,fee,reason?0:amount+fee,day,reason?'failed':'queued',reason,now);
      const result=this.job(source)!; this.state.db.exec('COMMIT'); return result;
    } catch(e) {this.state.db.exec('ROLLBACK');throw e;}
  }
}

