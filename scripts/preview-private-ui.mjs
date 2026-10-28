// Local, synthetic visual preview. No environment files or real wallet connections.
import {createServer} from 'node:http';
import {Readable} from 'node:stream';
import {writeFile,mkdir} from 'node:fs/promises';
import QRCode from 'qrcode';
import {accountRoutes} from '../dist/src/account.js';
import {State} from '../dist/src/state.js';
import {TipStore} from '../dist/src/tip-store.js';
import {loadConfig} from '../dist/src/config.js';
import {testUnifiedAddress} from '../dist/test/wallet-test-address.js';
import {PRIVATE_JS} from '../dist/src/account-ui.js';
const port=3013,baseUrl=`http://127.0.0.1:${port}`;
const c=loadConfig({PRIVATE_TIPPING_ENABLED:'true',ZCASH_NETWORK:'testnet',PUBLIC_BASE_URL:baseUrl,X_API_KEY:'synthetic',X_API_SECRET:'synthetic'});
const state=new State(':memory:'),store=new TipStore(state),login=store.login('200','your_account','testnet');
const address=testUnifiedAddress(1),account='11111111-1111-4111-8111-111111111111';
store.attachVerifiedTestnetWallet('200',account,address);
store.settings('200','testnet',{amount:'0.001',daily:'0.01',fee:'0.001',enabled:false,publicAck:false,consent:true},'0.01');
const backend={health:async()=>({network:'testnet',synced:true,currentConsensusVerified:true,backupConfirmed:true,accountCreationReady:true,depositsEnabled:true,transfersEnabled:false}),accounts:async()=>[],createAccount:async()=>{throw Error('Preview only');},shieldedAddress:async()=>{throw Error('Preview only');},balance:async()=>({network:'testnet',account,confirmedSpendableZat:500000,minimumConfirmations:10,transparentBalancePresent:false})};
const routes=accountRoutes(c,state,async()=>{throw Error('No outside calls in preview');},async()=>undefined,backend);
const demoQr=await QRCode.toDataURL('VISUAL PREVIEW ONLY — DO NOT FUND',{width:240,margin:4});
const banner='<aside style="position:sticky;top:0;z-index:99;padding:10px 20px;background:#191b18;color:#fff;font:12px system-ui;text-align:center">VISUAL PREVIEW · SAMPLE DATA · DO NOT FUND &nbsp; <a style="color:#f4b728" href="/">Home</a> &nbsp; <a style="color:#f4b728" href="/account">Dashboard</a> &nbsp; <a style="color:#f4b728" href="/login-preview">Sign-in</a></aside>';
function decorate(body){return body.replace('<body>','<body>'+banner).replaceAll(address,'DEMO-ADDRESS-NOT-FUNDABLE').replace(/data:image\/png;base64,[A-Za-z0-9+/=]+/g,demoQr);}
if(process.argv.includes('--export')){
 const directory=new URL('../../previews/',import.meta.url);await mkdir(directory,{recursive:true});
 for(const [path,name,authenticated] of [['/','home-preview.html',false],['/account','dashboard-preview.html',true],['/account','login-preview.html',false]]){
  const request=Readable.from([]);request.method='GET';request.url=path;request.headers={host:`127.0.0.1:${port}`,cookie:authenticated?'zec_account='+login.token:''};
  let html='';const response={setHeader(){},writeHead(){return response;},end(body){html=body??'';return response;}};
  await routes(request,response);
  html=decorate(html).replace('<script src="/assets/private.js?v=3" defer></script>','<script>document.addEventListener("DOMContentLoaded",()=>{'+PRIVATE_JS+'});</script>').replaceAll('href="/"','href="home-preview.html"').replaceAll('href="/account"','href="dashboard-preview.html"').replaceAll('href="/login-preview"','href="login-preview.html"').replaceAll('href="/#privacy"','href="home-preview.html#privacy"');
  // Preview forms cannot initiate a login or payment even when opened from a file.
  html=html.replaceAll('<form ','<form onsubmit="event.preventDefault();alert(\'Visual preview only. No changes are made.\');" ');
  await writeFile(new URL(name,directory),html);
 }
 state.close();console.log('Exported three synthetic HTML previews without fundable addresses.');process.exit(0);
}
createServer(async(req,res)=>{
 try{
  if(req.method!=='GET'&&req.method!=='HEAD'){res.writeHead(405).end('Visual preview only. No changes can be made.');return;}
  if(!['/','/account','/login-preview','/assets/private.js'].includes(new URL(req.url,baseUrl).pathname)){res.writeHead(404).end();return;}
  if(req.url==='/account')req.headers.cookie='zec_account='+login.token;
  if(req.url==='/login-preview')req.url='/account';
  const end=res.end.bind(res);
  res.end=(body,...args)=>{
   if(typeof body==='string'&&body.startsWith('<!doctype'))body=decorate(body);
   return end(body,...args);
  };
  if(!await routes(req,res))res.writeHead(404).end();
 }catch{res.writeHead(500).end('Preview could not render.');}
}).listen(port,'127.0.0.1',()=>console.log(`Synthetic visual preview: ${baseUrl}/account`));
