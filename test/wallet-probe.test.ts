import {test} from 'node:test';
import assert from 'node:assert/strict';
import {probeWallet} from '../src/wallet-probe.js';
test('wallet probe only discovers capabilities and never exposes credentials or enables signing',async()=>{
 const calls:unknown[]=[];const env={ZALLET_RPC_URL:'http://127.0.0.1:28232',ZALLET_RPC_USER:'probe-user',ZALLET_RPC_PASSWORD:'probe-secret'};
 const result=await probeWallet(env,(async(_url,init)=>{calls.push(JSON.parse(String(init?.body)));return Response.json({id:'capabilities',result:{methods:[{name:'pczt_create',params:[{name:'from'},{name:'amounts'}]}]}});}) as typeof fetch);
 assert.deepEqual(calls,[{jsonrpc:'2.0',id:'capabilities',method:'rpc.discover',params:[]}]);assert.equal(result.spendingEnabled,false);assert.equal(result.testnetVerified,false);assert.equal(result.capabilitiesPresent,false);assert.doesNotMatch(JSON.stringify(result),/probe-secret|probe-user/);
});
test('wallet probe rejects public endpoints, embedded credentials and uncorrelated RPC responses',async()=>{
 for(const endpoint of ['http://203.0.113.1:28232','https://127.0.0.1:28232','http://user:secret@127.0.0.1:28232','http://127.0.0.1:28232/proxy'])await assert.rejects(probeWallet({ZALLET_RPC_URL:endpoint}),/loopback/);
 await assert.rejects(probeWallet({ZALLET_RPC_USER:'x',ZALLET_RPC_PASSWORD:'y'},(async()=>Response.json({id:'wrong',result:{methods:[]}})) as typeof fetch),/invalid/);
});
test('wallet capability discovery accepts the observed wallet API without treating node broadcast as verified',async()=>{
 const names=['z_getnewaccount','z_getaddressforaccount','z_getbalanceforaccount','z_listaccounts','pczt_create','pczt_inspect','pczt_prove','pczt_sign','pczt_extract','getwalletstatus'];
 const result=await probeWallet({ZALLET_RPC_USER:'probe',ZALLET_RPC_PASSWORD:'private'},(async()=>Response.json({id:'capabilities',result:{methods:names.map(name=>({name,params:[]}))}})) as typeof fetch);
 assert.equal(result.capabilitiesPresent,true);assert.equal(result.nodeBroadcastChecked,false);assert.equal(result.spendingEnabled,false);assert.equal(result.testnetVerified,false);
 assert.ok(!result.methods.some(m=>m.name==='sendrawtransaction'));
});
