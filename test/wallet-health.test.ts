import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateWalletHealth,walletHealthPanel,readWalletHealth,WALLET_HEALTH_FILE} from '../src/wallet-health.js';
import {loadConfig} from '../src/config.js';
const now=1_800_000_000_000;
const sample=()=>({version:1,observedAt:now,network:'testnet',nodeRpc:true,walletRpc:true,nodeHeight:659600,nodeProgress:0.18294,walletStatus:'timeout'});
test('health is metadata only; stale, future, wrong-network and secret-bearing observations are rejected',()=>{
 assert.equal(validateWalletHealth(sample(),now).walletStatus,'timeout');
 for(const value of [{...sample(),network:'mainnet'},{...sample(),observedAt:now-120001},{...sample(),observedAt:now+30001},{...sample(),nodeHeight:-1},{...sample(),nodeProgress:Infinity},{...sample(),balance:1},{...sample(),password:'secret'},{...sample(),walletLocked:false}])assert.throws(()=>validateWalletHealth(value,now));
});
test('connected wallet status timeout is shown separately and never claims spending readiness',()=>{
 const html=walletHealthPanel(validateWalletHealth(sample(),now));
 assert.match(html,/Wallet API connected/);assert.match(html,/query timed out/);assert.match(html,/18\.3%/);assert.match(html,/availability is verified separately/);
 assert.doesNotMatch(html,/spending enabled|funds available|ready to send/i);
 const locked=walletHealthPanel(validateWalletHealth({...sample(),walletStatus:'available',walletLocked:true,walletHeight:444951},now));assert.match(locked,/spending remain locked/);assert.match(locked,/444,951/);
});
test('health file path is fixed and Mainnet cannot consume Testnet health',async()=>{
 assert.throws(()=>loadConfig({WALLET_HEALTH_FILE:'/app/.env',ZCASH_NETWORK:'testnet'}));
 assert.throws(()=>loadConfig({WALLET_HEALTH_FILE}));
 assert.equal(loadConfig({ZCASH_NETWORK:'testnet',WALLET_HEALTH_FILE}).walletHealthFile,WALLET_HEALTH_FILE);
 await assert.rejects(readWalletHealth('/app/.env'),/Invalid/);assert.equal(await readWalletHealth(''),undefined);
});
