import { TipStore, type TipJob } from './tip-store.js';
import {randomBytes} from 'node:crypto';
export type Proposal={network:'testnet';account:string;recipient:string;amountZat:number;feeZat:number;privacy:'FullPrivacy';transparentInputs:0;transparentOutputs:0;proposal:string};
export type SignedSpend={txid:string;raw:string};
// An adapter must implement this contract against a verified wallet release. No mock adapter is used by the app.
export interface TipWalletBackend {
  health():Promise<{network:'testnet';synced:boolean}>;
  prepare(input:{idempotencyKey:string;account:string;recipient:string;amountZat:number;maxFeeZat:number}):Promise<Proposal>;
  sign(input:{idempotencyKey:string;proposal:Proposal}):Promise<SignedSpend>;
  broadcast(transaction:SignedSpend):Promise<{txid:string}>;
  confirmations(txid:string):Promise<number>;
}
export class ProvenSpendRejection extends Error {
  constructor(message:string,readonly reason:'wallet_rejected'|'network_fee_limit'|'insufficient_funds'='wallet_rejected'){super(message);}
}
export class SpendLeaseLost extends Error {}
export class TipEngine {
  private activeJobs=new Set<string>();
  constructor(readonly store:TipStore,private wallet:TipWalletBackend) {
    store.state.db.exec('CREATE TABLE IF NOT EXISTS tip_spends (source TEXT PRIMARY KEY, proposal TEXT, signed TEXT, txid TEXT);CREATE TABLE IF NOT EXISTS tip_engine_leases(name TEXT PRIMARY KEY,owner TEXT NOT NULL,expires INTEGER NOT NULL)');
  }
  private async healthy() {const status=await this.wallet.health();if(status.network!=='testnet'||!status.synced)throw new Error('Wallet is not synchronized on Testnet.');}
  private active(job:TipJob) {return !!this.store.withdrawal(job.source)||(this.store.profile(job.sender,job.network)?.enabled===1 && !this.store.state.user(job.sender)?.opted_out);}
  async process(source:string):Promise<void> {
    if(this.activeJobs.has(source))return;
    const db=this.store.state.db,owner=randomBytes(24).toString('hex'),now=Date.now();
    const acquired=db.prepare("INSERT INTO tip_engine_leases VALUES ('spend',?,?) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires=excluded.expires WHERE tip_engine_leases.expires<?").run(owner,now+90_000,now);
    if(!acquired.changes)return;
    this.activeJobs.add(source);let lost=false;
    const fence=()=>{
      if(lost||!db.prepare("UPDATE tip_engine_leases SET expires=? WHERE name='spend' AND owner=? AND expires>?").run(Date.now()+90_000,owner,Date.now()).changes){lost=true;throw new SpendLeaseLost('Wallet processing lease expired.');}
    };
    const renew=setInterval(()=>{try{fence();}catch{lost=true;}},20_000);renew.unref();
    try {await this.processOne(source,fence,owner);}catch(error){if(!(error instanceof SpendLeaseLost))throw error;}
    finally{clearInterval(renew);db.prepare("DELETE FROM tip_engine_leases WHERE name='spend' AND owner=?").run(owner);this.activeJobs.delete(source);}
  }
  private async processOne(source:string,fence:()=>void,owner:string):Promise<void> {
    fence();
    let job=this.store.job(source);if(!job || job.network!=='testnet')return;
    const db=this.store.state.db;
    const transition=(from:string,to:string,reason:string|null=null)=>{
      fence();
      return db.prepare("UPDATE tip_jobs SET status=?,reason=? WHERE source=? AND status=? AND EXISTS (SELECT 1 FROM tip_engine_leases WHERE name='spend' AND owner=? AND expires>?)").run(to,reason,source,from,owner,Date.now()).changes>0;
    };
    // A crash during preparation/signing is held for reconciliation, never repeated with new transaction material.
    if(job.status==='preparing'||job.status==='signing') {transition(job.status,'review','interrupted_wallet_step');return;}
    if(job.status==='queued') {
      if(!this.active(job)){transition('queued','failed','tips_paused');return;}
      await this.healthy();fence();
      if(!transition('queued','preparing'))return;
      const from=this.store.wallet(job.sender,job.network), to=this.store.wallet(job.recipient,job.network),withdrawal=this.store.withdrawal(source);
      if(from?.status!=='ready'||(!withdrawal&&to?.status!=='ready')){transition('preparing','failed','wallet_unavailable');return;}
      const destination=withdrawal?.address??to!.address;
      try {
        const proposal=await this.wallet.prepare({idempotencyKey:source,account:from.account,recipient:destination,amountZat:job.amount_zat,maxFeeZat:job.fee_zat});fence();
        if(proposal.network!=='testnet'||proposal.account!==from.account||proposal.recipient!==destination||proposal.amountZat!==job.amount_zat||!Number.isSafeInteger(proposal.feeZat)||proposal.feeZat<0||proposal.feeZat>job.fee_zat||proposal.privacy!=='FullPrivacy'||proposal.transparentInputs!==0||proposal.transparentOutputs!==0||typeof proposal.proposal!=='string'||!proposal.proposal||proposal.proposal.length>2_000_000)throw new Error('Wallet proposal failed validation.');
        db.exec('BEGIN IMMEDIATE');try {fence();
          db.prepare('INSERT INTO tip_spends(source,proposal) VALUES (?,?)').run(source,JSON.stringify(proposal));
          if(!transition('preparing','prepared'))throw new Error('State changed.');db.exec('COMMIT');
        }catch(e){db.exec('ROLLBACK');throw e;}
      }catch(e){transition('preparing',e instanceof ProvenSpendRejection?'failed':'review',e instanceof ProvenSpendRejection?e.reason:'wallet_step_uncertain');return;}
    }
    job=this.store.job(source)!;
    if(job.status==='prepared') {
      if(!this.active(job)){transition('prepared','review','tips_paused');return;}
      await this.healthy();fence();
      if(!transition('prepared','signing'))return;
      try {
        const row=db.prepare('SELECT proposal FROM tip_spends WHERE source=?').get(source) as {proposal:string};
        const signed=await this.wallet.sign({idempotencyKey:source,proposal:JSON.parse(row.proposal) as Proposal});fence();
        if(!/^[a-f0-9]{64}$/.test(signed.txid)||!/^([a-f0-9]{2})+$/.test(signed.raw)||signed.raw.length>4_000_000)throw new Error('Invalid signed transaction.');
        db.exec('BEGIN IMMEDIATE');try {fence();
          db.prepare('UPDATE tip_spends SET signed=?,txid=? WHERE source=?').run(JSON.stringify(signed),signed.txid,source);
          if(!transition('signing','signed'))throw new Error('State changed.');db.exec('COMMIT');
        }catch(e){db.exec('ROLLBACK');throw e;}
      }catch {transition('signing','review','wallet_step_uncertain');return;}
    }
    job=this.store.job(source)!;
    if(job.status==='signed'||job.status==='broadcasting') {
      if(!this.active(job)){transition(job.status,'review','tips_paused');return;}
      await this.healthy();fence();
      const row=db.prepare('SELECT signed FROM tip_spends WHERE source=?').get(source) as {signed:string};
      const signed=JSON.parse(row.signed) as SignedSpend;
      if(job.status==='signed'&&!transition('signed','broadcasting'))return;
      try {
        // The exact persisted signed bytes may be rebroadcast after a timeout. Never create another spend.
        const result=await this.wallet.broadcast(signed);fence();
        if(result.txid!==signed.txid){transition('broadcasting','review','broadcast_mismatch');return;}
        transition('broadcasting','submitted');
      }catch {return;}
    }
    job=this.store.job(source)!;
    if(job.status==='submitted'||job.status==='confirmed') {
      await this.healthy();fence();
      const txid=(db.prepare('SELECT txid FROM tip_spends WHERE source=?').get(source) as {txid:string}).txid;
      const confirmations=await this.wallet.confirmations(txid);fence();
      if(!Number.isSafeInteger(confirmations)||confirmations<0)throw new Error('Invalid chain confirmation result.');
      if(confirmations>=3)transition('submitted','confirmed');
      else if(job.status==='confirmed')transition('confirmed','submitted','chain_reorganization');
    }
  }
}

