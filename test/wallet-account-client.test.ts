import {test} from 'node:test';
import assert from 'node:assert/strict';
import {bech32} from '@scure/base';
import {WalletAccountClient,WALLET_ACCOUNT_SOCKET,type AccountTransport} from '../src/wallet-account-client.js';
import {loadConfig} from '../src/config.js';
const account='11111111-1111-4111-8111-111111111111';
const address=bech32.encode('ztestsapling',bech32.toWords(Buffer.alloc(43,1)),200);
const ready={network:'testnet',synced:false,currentConsensusVerified:true,backupConfirmed:true,accountCreationReady:true,depositsEnabled:false,transfersEnabled:false};

test('account client preserves empty bootstrap separately from spending readiness',async()=>{
 const client=new WalletAccountClient(WALLET_ACCOUNT_SOCKET,async()=>ready);
 assert.deepEqual(await client.health(),ready);
 for(const change of [{network:'mainnet'},{accountCreationReady:undefined},{transfersEnabled:'true'}]){
  await assert.rejects(new WalletAccountClient(WALLET_ACCOUNT_SOCKET,async()=>({...ready,...change})).health());
 }
 assert.throws(()=>new WalletAccountClient('/tmp/arbitrary-wallet.sock'));
});

test('account client uses only scoped endpoints and validates shielded address ownership',async()=>{
 const calls:unknown[]=[];
 const transport:AccountTransport=async(method,path,body)=>{
  calls.push({method,path,body});
  if(path==='/accounts')return method==='GET'?[{account,name:'tip-'+'a'.repeat(32)}]:{account};
  return {network:'testnet',account,address};
 };
 const client=new WalletAccountClient(WALLET_ACCOUNT_SOCKET,transport);
 await client.accounts();await client.createAccount('tip-'+'a'.repeat(32));
 assert.equal((await client.shieldedAddress(account,0)).address,address);
 assert.deepEqual(calls,[{method:'GET',path:'/accounts',body:undefined},
  {method:'POST',path:'/accounts',body:{label:'tip-'+'a'.repeat(32)}},
  {method:'POST',path:'/address',body:{account,index:0}}]);
 await assert.rejects(client.createAccount('alice'));
 const wrong=new WalletAccountClient(WALLET_ACCOUNT_SOCKET,async()=>({network:'testnet',account:'22222222-2222-4222-8222-222222222222',address}));
 await assert.rejects(wrong.shieldedAddress(account,0));
 await assert.rejects(new WalletAccountClient(WALLET_ACCOUNT_SOCKET,async()=>({network:'testnet',account,address:'utest1invalid'})).shieldedAddress(account,0));
});

test('account feature cannot be configured with Mainnet, public request mode or arbitrary socket',()=>{
 const valid={PRIVATE_TIPPING_ENABLED:'true',ZCASH_NETWORK:'testnet',WALLET_ACCOUNTS_ENABLED:'true',WALLET_BROKER_SOCKET:WALLET_ACCOUNT_SOCKET};
 assert.equal(loadConfig(valid).walletAccounts,true);
 for(const change of [{ZCASH_NETWORK:'mainnet'},{PRIVATE_TIPPING_ENABLED:'false'},{WALLET_BROKER_SOCKET:'/tmp/other.sock'}])assert.throws(()=>loadConfig({...valid,...change}));
 assert.equal(loadConfig({}).walletAccounts,false);
});

test('confirmed balance is account-bound and cannot silently change units or confirmation policy',async()=>{
 const value={network:'testnet',account,confirmedSpendableZat:12345,minimumConfirmations:10,transparentBalancePresent:false};
 const client=new WalletAccountClient(WALLET_ACCOUNT_SOCKET,async(method,path)=>{assert.equal(method,'GET');assert.equal(path,'/balance/'+account);return value;});
 assert.equal((await client.balance(account)).confirmedSpendableZat,12345);
 for(const change of [{account:'22222222-2222-4222-8222-222222222222'},{minimumConfirmations:1},{confirmedSpendableZat:-1},{confirmedSpendableZat:0.1}]){
  await assert.rejects(new WalletAccountClient(WALLET_ACCOUNT_SOCKET,async()=>({...value,...change})).balance(account));
 }
});
