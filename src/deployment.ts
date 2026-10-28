import { isAbsolute } from 'node:path';
import { loadConfig, requirePublic, requireUser } from './config.js';
export function deploymentReport(env: NodeJS.ProcessEnv) {
  const checks: {name:string;ok:boolean;detail:string}[] = [];
  const add=(name:string,ok:boolean,detail:string)=>checks.push({name,ok,detail});
  let c:ReturnType<typeof loadConfig>;
  try {c=loadConfig(env);} catch {return {ok:false,checks:[{name:'configuration',ok:false,detail:'Configuration is invalid. Values were not printed.'}]};}
  const mode=env.BOT_MODE??'serve';
  add('run mode',['serve','start'].includes(mode),'Use serve for website-only launch, or start for the website and automatic worker.');
  let publicOk=true;try {requirePublic(c);}catch{publicOk=false;}
  const host=new URL(c.baseUrl).hostname;
  const stable=publicOk && !host.endsWith('.trycloudflare.com') && !/(^|\.)(example\.(com|org|net)|invalid|test)$/.test(host);
  add('stable HTTPS',stable,'Use your stable HTTPS hostname; temporary tunnels and example hostnames are not deployment targets.');
  const domain=env.DOMAIN??'';
  add('TLS hostname',/^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$/.test(domain)&&domain.toLowerCase()===host.toLowerCase(),'DOMAIN must match the bare DNS hostname in PUBLIC_BASE_URL.');
  add('persistent database',isAbsolute(c.dataDir),'DATA_DIR must point to an absolute persistent disk path; the container uses /app/data.');
  add('HTTP binding',c.webHost==='0.0.0.0','The container must listen on 0.0.0.0 behind its private reverse proxy.');
  if(mode==='start') {
    let userOk=true;try{requireUser(c);}catch{userOk=false;}
    add('bot credentials',userOk,'Existing bot credentials must be present. This does not contact X or verify identity.');
    add('posting switch',c.posting,'Automatic replies require X_POSTING_ENABLED=true and may publish pending eligible replies.');
  } else add('website-only launch',!c.posting,'Keep posting disabled for the initial website-only launch.');
  if(c.privateTips) {
    let callbackOk=false;try {const callback=new URL(c.userCallback);callbackOk=callback.origin===c.baseUrl && callback.pathname==='/account/callback' && !callback.search && !callback.hash;}catch{}
    add('private signup callback',callbackOk,'The additional X-user callback must be PUBLIC_BASE_URL/account/callback. Register it in the existing X app.');
  }
  add('wallet isolation',!c.hostedWallets||(c.privateTips&&c.walletAccounts&&c.network==='testnet'),'Wallet processing requires private Testnet accounts and the fixed local broker socket. Operator controls and verified recovery separately gate deposits and transfers.');
  return {ok:checks.every(c=>c.ok),checks};
}

