import {createHash} from 'node:crypto';
import {request} from 'node:http';
import {WalletAccountClient,WALLET_ACCOUNT_SOCKET} from './wallet-account-client.js';
import {ProvenSpendRejection,type Proposal,type SignedSpend,type TipWalletBackend} from './tip-engine.js';
import {validateAddress} from './zcash.js';
export class TransferUnavailable extends Error {constructor(){super('Wallet processing is unavailable or needs review. No new payment will be prepared automatically.');}}
export type TransferTransport=(method:'GET'|'POST',path:string,body?:Record<string,unknown>)=>Promise<unknown>;
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const uuid=(v:string)=>/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(v);
const hex=(v:unknown):v is string=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const integer=(v:unknown,max:number)=>Number.isSafeInteger(v)&&Number(v)>0&&Number(v)<=max;
const key=(v:string)=>createHash('sha256').update('zec-tip-bot:testnet:'+v).digest('hex');
function socketTransport(socket:string):TransferTransport {
 if(socket!==WALLET_ACCOUNT_SOCKET)throw new TransferUnavailable();
 return (method,path,body)=>new Promise((resolve,reject)=>{
  const data=body?Buffer.from(JSON.stringify(body)):undefined;
  const fail=()=>reject(new TransferUnavailable());
  const call=request({socketPath:socket,path,method,headers:data?{'Content-Type':'application/json','Content-Length':data.length}:{}},response=>{
   const chunks:Buffer[]=[];let size=0;
   response.on('data',(chunk:Buffer)=>{size+=chunk.length;if(size>4_100_000){response.destroy();call.destroy();fail();}else chunks.push(chunk);});
   response.on('error',fail);response.on('aborted',fail);
   response.on('end',()=>{try{
    const value:unknown=JSON.parse(Buffer.concat(chunks).toString());
    if(response.statusCode!==200){
     if(path==='/prepare'&&response.statusCode===409&&object(value)&&value.error==='proven_rejection')reject(new ProvenSpendRejection('The wallet rejected the payment before signing.'));
     else if(path==='/prepare'&&response.statusCode===409&&object(value)&&value.error==='insufficient_funds')reject(new ProvenSpendRejection('Insufficient available funds for the tip and its network fee.','insufficient_funds'));
     else if(path==='/prepare'&&response.statusCode===409&&object(value)&&value.error==='proven_fee_rejection')reject(new ProvenSpendRejection('The required network fee exceeds your saved ceiling.','network_fee_limit'));
     else fail();return;
    }
    resolve(value);
   }catch{fail();}});
  });
  // There are no automatic retries, including after a socket timeout. The broker
  // persists uncertain mutations and the engine holds them for reconciliation.
  const timer=setTimeout(()=>{call.destroy();fail();},350_000);timer.unref();
  call.once('close',()=>{clearTimeout(timer);fail();});call.once('error',fail);
  if(data)call.write(data);call.end();
 });
}
export class WalletTransferClient implements TipWalletBackend {
 private transport:TransferTransport;
 constructor(socket=WALLET_ACCOUNT_SOCKET,transport?:TransferTransport){this.transport=transport??socketTransport(socket);}
 async health(){
  const health=await new WalletAccountClient(WALLET_ACCOUNT_SOCKET,this.transport).health();
  if(!health.transfersEnabled||!health.backupConfirmed||!health.currentConsensusVerified)throw new TransferUnavailable();
  return {network:health.network,synced:health.synced};
 }
 async prepare(input:{idempotencyKey:string;account:string;recipient:string;amountZat:number;maxFeeZat:number}):Promise<Proposal>{
  if(!/^(?:\d{1,20}|withdraw-[a-f0-9]{32})$/.test(input.idempotencyKey)||!uuid(input.account)||!integer(input.amountZat,1_000_000)||!integer(input.maxFeeZat,100_000))throw new ProvenSpendRejection('Payment exceeds the Testnet pilot limits.');
  const destination=validateAddress(input.recipient,'testnet');
  if(destination.transparentReceiver||destination.kind!=='unified')throw new ProvenSpendRejection('A shielded-only Testnet Unified Address is required.');
  const value=await this.transport('POST','/prepare',{...input,idempotencyKey:key(input.idempotencyKey)});
  if(!object(value)||value.network!=='testnet'||value.account!==input.account||value.recipient!==destination.address||value.amountZat!==input.amountZat||!Number.isSafeInteger(value.feeZat)||Number(value.feeZat)<0||Number(value.feeZat)>input.maxFeeZat||value.privacy!=='FullPrivacy'||value.transparentInputs!==0||value.transparentOutputs!==0||typeof value.proposal!=='string'||!/^[a-f0-9]{48}$/.test(value.proposal))throw new TransferUnavailable();
  return value as Proposal;
 }
 async sign(input:{idempotencyKey:string;proposal:Proposal}):Promise<SignedSpend>{
  if(!/^(?:\d{1,20}|withdraw-[a-f0-9]{32})$/.test(input.idempotencyKey)||!/^[a-f0-9]{48}$/.test(input.proposal.proposal))throw new TransferUnavailable();
  const value=await this.transport('POST','/sign',{idempotencyKey:key(input.idempotencyKey),proposal:input.proposal.proposal});
  if(!object(value)||!hex(value.txid)||typeof value.raw!=='string'||value.raw.length>4_000_000||!/^([a-f0-9]{2})+$/.test(value.raw))throw new TransferUnavailable();
  return {txid:value.txid,raw:value.raw};
 }
 async broadcast(transaction:SignedSpend){
  if(!hex(transaction.txid)||!/^([a-f0-9]{2})+$/.test(transaction.raw)||transaction.raw.length>4_000_000)throw new TransferUnavailable();
  const value=await this.transport('POST','/broadcast',transaction);
  if(!object(value)||value.txid!==transaction.txid)throw new TransferUnavailable();
  return {txid:transaction.txid};
 }
 async confirmations(txid:string){
  if(!hex(txid))throw new TransferUnavailable();
  const value=await this.transport('GET','/confirmations/'+txid);
  if(!object(value)||!Number.isSafeInteger(value.confirmations)||Number(value.confirmations)<0||Number(value.confirmations)>10_000_000)throw new TransferUnavailable();
  return value.confirmations as number;
 }
}
