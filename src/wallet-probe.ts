import type { Config } from './config.js';
export async function probeWallet(env:NodeJS.ProcessEnv,fetcher:typeof fetch=fetch) {
  const endpoint=new URL(env.ZALLET_RPC_URL??'http://127.0.0.1:28232');
  if(endpoint.protocol!=='http:' || !['127.0.0.1','[::1]'].includes(endpoint.hostname) || endpoint.username || endpoint.password || endpoint.pathname!=='/' || endpoint.search || endpoint.hash)throw new Error('Wallet probe requires an explicit loopback HTTP RPC endpoint.');
  const user=env.ZALLET_RPC_USER??'', password=env.ZALLET_RPC_PASSWORD??'';
  if(!user || !password || /[:\r\n]/.test(user) || /[\r\n]/.test(password))throw new Error('Wallet RPC credentials must be entered locally.');
  let response:Response;
  try {response=await fetcher(endpoint,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Basic '+Buffer.from(user+':'+password).toString('base64')},body:JSON.stringify({jsonrpc:'2.0',id:'capabilities',method:'rpc.discover',params:[]}),redirect:'error',signal:AbortSignal.timeout(15_000)});}
  catch {throw new Error('Wallet RPC could not be reached.');}
  if(!response.ok)throw new Error('Wallet RPC denied the capability check.');
  const payload=await response.json() as {id?:unknown;error?:unknown;result?:{methods?:{name?:unknown;params?:{name?:unknown}[]}[]}};
  if(payload.id!=='capabilities'||payload.error||!Array.isArray(payload.result?.methods))throw new Error('Wallet capability response was invalid.');
  // The verified wallet exposes PCZT extraction; the separate full node broadcasts raw transactions.
  const required=['z_getnewaccount','z_getaddressforaccount','z_getbalanceforaccount','z_listaccounts','pczt_create','pczt_inspect','pczt_prove','pczt_sign','pczt_extract','getwalletstatus'];
  const methods=required.map(name=>{const method=payload.result!.methods!.find(m=>m.name===name);return {name,available:!!method,arguments:(method?.params??[]).flatMap(p=>typeof p.name==='string' && /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(p.name)?[p.name]:[])};});
  // This report has schema names only, never account data, addresses, credentials or raw upstream errors.
  return {readOnly:true,capabilitiesPresent:methods.every(m=>m.available),nodeBroadcastChecked:false,testnetVerified:false,spendingEnabled:false,methods};
}
