import {request} from 'node:http';
import type {AccountWalletBackend,AccountWalletHealth,WalletBalance,WalletReceipt} from './wallet-accounts.js';
import {validateAddress} from './zcash.js';
export const WALLET_ACCOUNT_SOCKET='/run/zec-tip-bot-wallet-broker/wallet.sock';
const uuid=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
const object=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
export class WalletAccountUnavailable extends Error {
 constructor(){super('The wallet account service is not ready. No transfer was sent.');}
}
export type AccountTransport=(method:'GET'|'POST',path:string,body?:Record<string,unknown>)=>Promise<unknown>;
function socketTransport(socket:string):AccountTransport {
 if(socket!==WALLET_ACCOUNT_SOCKET)throw new WalletAccountUnavailable();
 return (method,path,body)=>new Promise((resolve,reject)=>{
  const data=body?Buffer.from(JSON.stringify(body)):undefined;
  const fail=()=>reject(new WalletAccountUnavailable());
  const call=request({socketPath:socket,path,method,headers:data?{'Content-Type':'application/json','Content-Length':data.length}:{}},response=>{
   let size=0;const chunks:Buffer[]=[];
   response.on('data',(chunk:Buffer)=>{size+=chunk.length;if(size>64_000){response.destroy();call.destroy();fail();}else chunks.push(chunk);});
   response.on('error',fail);
   response.on('aborted',fail);
   response.on('end',()=>{if(response.statusCode!==200){fail();return;}try{resolve(JSON.parse(Buffer.concat(chunks).toString()));}catch{fail();}});
  });
  // One total timeout, including queued local RPC calls; never retry a mutation.
  const timer=setTimeout(()=>{call.destroy();fail();},55_000);timer.unref();
  call.once('close',()=>{clearTimeout(timer);fail();});call.once('error',fail);
  if(data)call.write(data);call.end();
 });
}
export class WalletAccountClient implements AccountWalletBackend {
 private transport:AccountTransport;
 constructor(socket=WALLET_ACCOUNT_SOCKET,transport?:AccountTransport){this.transport=transport??socketTransport(socket);}
 async health():Promise<AccountWalletHealth>{
  const value=await this.transport('GET','/health');
  if(!object(value)||value.network!=='testnet'||['synced','currentConsensusVerified','backupConfirmed','accountCreationReady','depositsEnabled','transfersEnabled'].some(key=>typeof value[key]!=='boolean'))throw new WalletAccountUnavailable();
  return {network:'testnet',synced:value.synced as boolean,currentConsensusVerified:value.currentConsensusVerified as boolean,
   backupConfirmed:value.backupConfirmed as boolean,accountCreationReady:value.accountCreationReady as boolean,
   depositsEnabled:value.depositsEnabled as boolean,transfersEnabled:value.transfersEnabled as boolean};
 }
 async accounts(){
  const value=await this.transport('GET','/accounts');if(!Array.isArray(value)||value.length>100)throw new WalletAccountUnavailable();
  return value.map(row=>{if(!object(row)||!uuid(row.account)||typeof row.name!=='string'||row.name.length>128)throw new WalletAccountUnavailable();return {account:row.account,name:row.name};});
 }
 async createAccount(name:string){
  if(!/^tip-[a-f0-9]{32}$/.test(name))throw new WalletAccountUnavailable();
  const value=await this.transport('POST','/accounts',{label:name});
  if(!object(value)||!uuid(value.account))throw new WalletAccountUnavailable();return {account:value.account};
 }
 async shieldedAddress(account:string,index:0){
  if(!uuid(account)||index!==0)throw new WalletAccountUnavailable();
  const value=await this.transport('POST','/address',{account,index});
  if(!object(value)||value.network!=='testnet'||value.account!==account||typeof value.address!=='string')throw new WalletAccountUnavailable();
  const address=validateAddress(value.address,'testnet');if(address.transparentReceiver)throw new WalletAccountUnavailable();
  return {network:'testnet' as const,account,address:address.address};
 }
 async balance(account:string):Promise<WalletBalance>{
  if(!uuid(account))throw new WalletAccountUnavailable();
  const value=await this.transport('GET','/balance/'+account);
  if(!object(value)||value.network!=='testnet'||value.account!==account||!Number.isSafeInteger(value.confirmedSpendableZat)||Number(value.confirmedSpendableZat)<0||Number(value.confirmedSpendableZat)>2_100_000_000_000_000||value.minimumConfirmations!==10||typeof value.transparentBalancePresent!=='boolean')throw new WalletAccountUnavailable();
  return {network:'testnet',account,confirmedSpendableZat:value.confirmedSpendableZat as number,minimumConfirmations:10,transparentBalancePresent:value.transparentBalancePresent};
 }
 async receipts(account:string){
  if(!uuid(account))throw new WalletAccountUnavailable();
  const value=await this.transport('GET','/receipts/'+account);
  if(!object(value)||value.network!=='testnet'||value.account!==account||!Array.isArray(value.receipts)||value.receipts.length>30)throw new WalletAccountUnavailable();
  const receipts:WalletReceipt[]=value.receipts.map(row=>{
   if(!object(row)||typeof row.receipt!=='string'||!/^[a-f0-9]{64}$/.test(row.receipt)||!Number.isSafeInteger(row.amountZat)||Number(row.amountZat)<=0||Number(row.amountZat)>2_100_000_000_000_000||!Number.isSafeInteger(row.confirmations)||Number(row.confirmations)<0||Number(row.confirmations)>100_000_000||(row.time!==null&&(!Number.isSafeInteger(row.time)||Number(row.time)<0||Number(row.time)>4_102_444_800)))throw new WalletAccountUnavailable();
   return {receipt:row.receipt,amountZat:row.amountZat as number,confirmations:row.confirmations as number,time:row.time as number|null};
  });
  if(new Set(receipts.map(row=>row.receipt)).size!==receipts.length)throw new WalletAccountUnavailable();
  return {network:'testnet' as const,account,receipts};
 }
}
