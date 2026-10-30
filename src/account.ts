import type { IncomingMessage, ServerResponse } from 'node:http';
import { randomBytes,createHash } from 'node:crypto';
import {beginUserSignin,finishUserSignin,UserAuthError} from './user-auth.js';
import type { Config } from './config.js';
import type { State } from './state.js';
import { TipStore, formatZat } from './tip-store.js';
import QRCode from 'qrcode';
import {privateLayout,privateHome,privateLogin,privateDashboard,walletLiveParts,PRIVATE_JS,LEGACY_PRIVATE_JS,glyph} from './account-ui.js';
import {readWalletHealth,walletHealthPanel,type WalletHealth} from './wallet-health.js';
import {WalletAccountProvisioner,type AccountWalletBackend,type AccountWalletHealth,type WalletBalance} from './wallet-accounts.js';
import {WalletAccountClient} from './wallet-account-client.js';
import {validateAddress,zatoshis} from './zcash.js';
const escape=(s:string)=>s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
function cookie(req:IncomingMessage,name:string) { const entries=(req.headers.cookie??'').split(';').map(s=>s.trim()).filter(s=>s.startsWith(name+'='));return entries.length===1?entries[0]!.slice(name.length+1):''; }
async function fields(req:IncomingMessage) {
  if ((req.headers['content-type']??'').split(';')[0]!=='application/x-www-form-urlencoded') throw new Error('Invalid form.');
  let size=0; const parts:Buffer[]=[];
  for await(const chunk of req) {const bytes=Buffer.from(chunk);size+=bytes.length;if(size>4096)throw new Error('Invalid form.');parts.push(bytes);}
  const values=new URLSearchParams(Buffer.concat(parts).toString('utf8'));
  for(const key of values.keys())if(values.getAll(key).length!==1)throw new Error('Invalid form.');
  return values;
}
const reasons:Record<string,string>={insufficient_funds:'Insufficient available funds for this payment and its network fee. Add funds or lower the amount, then submit a new command. No payment was sent.',wallet_rejected:'The wallet rejected this payment before signing. Check available funds and try a new command.',network_fee_limit:'Network fee exceeds your saved limit. No payment was sent.',setup_required:'Configure and enable tipping first.',wallet_unavailable:'Both people need ready wallets. Ask the recipient to sign in and create one, then post a fresh tip command. No payment was sent.',self_tip:'Self-tipping is disabled.',daily_limit:'Your daily spending limit would be exceeded.',stale_command:'The command predates your latest settings or is too old.',mainnet_disabled:'Hosted Mainnet transfers are disabled.',tips_paused:'Automatic tips are paused.'};
function page(body:string) {return privateLayout(/^<section class="(?:dashboard|private-hero|login-layout)"/.test(body)?body:'<div class="review-page">'+body+'</div>');}
export function accountRoutes(c:Config,state:State,fetcher:typeof fetch=fetch,healthReader:(path:string)=>Promise<WalletHealth|undefined>=readWalletHealth,backend:AccountWalletBackend|undefined=c.walletAccounts?new WalletAccountClient(c.walletBrokerSocket):undefined) {
  const store=new TipStore(state), origin=new URL(c.baseUrl), secure=origin.protocol==='https:';
  const provisioner=backend?new WalletAccountProvisioner(store,backend):undefined;
  if (!secure && origin.hostname!=='127.0.0.1') throw new Error('Private accounts require HTTPS or explicit loopback.');
  const callback=new URL(c.userCallback);
  if(callback.origin!==origin.origin || callback.pathname!=='/account/callback' || callback.search || callback.hash)throw new Error('Invalid X_USER_CALLBACK_URL.');
  const sessionName=secure?'__Host-zec_account':'zec_account', loginName=secure?'__Host-zec_login':'zec_login';
  const cookieValue=(name:string,value:string,age:number)=>`${name}=${value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${age}${secure?'; Secure':''}`;
  const pending=new Map<string,{verifier:string;browser:string;expires:number}>();
  return async(req:IncomingMessage,res:ServerResponse):Promise<boolean>=>{
    const url=new URL(req.url??'/',origin), path=url.pathname;
    if(path==='/assets/private.js'){
      if(req.headers.host!==origin.host||!['GET','HEAD'].includes(req.method??'')){res.writeHead(400).end();return true;}
      res.setHeader('Content-Type','application/javascript; charset=utf-8');res.setHeader('Cache-Control',['3','4'].includes(url.searchParams.get('v')??'')?'public, max-age=31536000, immutable':'public, max-age=300');res.setHeader('X-Content-Type-Options','nosniff');
      res.end(req.method==='HEAD'?'':url.searchParams.get('v')==='3'?LEGACY_PRIVATE_JS:PRIVATE_JS);return true;
    }
    if(path!=='/' && !path.startsWith('/account'))return false;
    res.setHeader('Cache-Control','no-store');// Keep same-origin form origin checks working; do not send callback query referrers.
    res.setHeader('Referrer-Policy',path==='/account/callback'?'no-referrer':'same-origin');res.setHeader('X-Robots-Tag','noindex, nofollow');
    res.setHeader('Content-Security-Policy',"default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'self' https://api.x.com https://x.com https://api.twitter.com https://twitter.com; base-uri 'none'; frame-ancestors 'none'");
    res.setHeader('Content-Type','text/html; charset=utf-8');
    if(req.headers.host!==origin.host){res.writeHead(400).end('Invalid host.');return true;}
    if(!['GET','POST','HEAD'].includes(req.method??'')){res.writeHead(405).end();return true;}
    if(path==='/' && ['GET','HEAD'].includes(req.method??'')){
      res.setHeader('X-Robots-Tag','index, follow');
      res.end(req.method==='HEAD'?'':privateLayout(privateHome(),origin.origin));return true;
    }
    if(path==='/account/live'&&req.method!=='GET'){res.writeHead(405).end();return true;}
    if(req.method==='HEAD'){res.end();return true;}
    const token=cookie(req,sessionName), session=store.session(token,c.network);
    const send=(body:string,status=200)=>{res.writeHead(status).end(page(body));};
    const redirect=(target:string)=>res.writeHead(303,{Location:target}).end();
    try {
      if(req.method==='GET' && path==='/account' && !session) {
        const nonce=randomBytes(24).toString('hex');res.setHeader('Set-Cookie',cookieValue(loginName,nonce,600));
        send(privateLogin(nonce));return true;
      }
      if(req.method==='POST' && path==='/account/login') {
        const form=await fields(req), browser=cookie(req,loginName);
        if(req.headers.origin!==origin.origin || !/^[a-f0-9]{48}$/.test(browser) || form.get('csrf')!==browser){send('<h1>Sign-in could not be verified.</h1><p>Open a fresh account page and try again. Cookies must be enabled for this site.</p><a class="button" href="/account">Open sign-in page</a>',403);return true;}
        for(const [key,value] of pending)if(value.expires<=Date.now())pending.delete(key);
        if(pending.size>=100){send('Sign-in is busy. Please try later.',429);return true;}
        if(!c.userClientId||!c.userClientSecret){send('<h1>Sign-in is being updated.</h1><p>Please try again shortly.</p>',503);return true;}
        const attempt=beginUserSignin(c);
        pending.set(attempt.state,{verifier:attempt.verifier,browser,expires:Date.now()+600_000});
        redirect(attempt.url);return true;
      }
      if(req.method==='GET' && path==='/account/callback') {
        const loginState=url.searchParams.get('state')??'', code=url.searchParams.get('code')??'', entry=pending.get(loginState);
        const invalid=!entry||entry.expires<=Date.now()||entry.browser!==cookie(req,loginName)||url.searchParams.getAll('state').length!==1||
          [...url.searchParams.keys()].some(key=>!['state','code','error','error_description'].includes(key));
        if(invalid){send('Sign-in expired or could not be matched. Open your account again.',403);return true;}
        if(url.searchParams.has('error')){
          pending.delete(loginState);res.setHeader('Set-Cookie',cookieValue(loginName,'',0));
          send('<h1>Sign-in was not completed.</h1><a href="/account">Back to sign-in</a>',403);return true;
        }
        if(url.searchParams.getAll('code').length!==1||!code||code.length>2048||/[\x00-\x20\x7f]/.test(code)){send('Sign-in expired or could not be matched. Open your account again.',403);return true;}
        pending.delete(loginState); // One use, including failed exchanges; never retry an authorization code.
        const identity=await finishUserSignin(c,code,entry!.verifier,()=>state.reserveCost(10,c.budgetMills),fetcher);
        // Only identity survives sign-in. Tokens and PKCE verifiers are never written to .env or SQLite.
        if(session)store.logout(token);
        const logged=store.login(identity.id,identity.username,c.network);
        res.setHeader('Set-Cookie',[cookieValue(sessionName,logged.token,3600),cookieValue(loginName,'',0)]);redirect('/account');return true;
      }
      if(path==='/account/live'&&!session){res.setHeader('Content-Type','application/json; charset=utf-8');res.writeHead(401).end(JSON.stringify({error:'signin_required'}));return true;}
      if(!session){redirect('/account');return true;}
      if(req.method==='POST') {
        const form=await fields(req);
        if(req.headers.origin!==origin.origin || form.get('csrf')!==session.csrf){send('This form expired. Reload your account.',403);return true;}
        if(path==='/account/logout'){store.logout(token);res.setHeader('Set-Cookie',cookieValue(sessionName,'',0));redirect('/account');return true;}
        if(path==='/account/wallet') {
          if(!c.walletAccounts||!provisioner||c.network!=='testnet'){send('<h1>Wallet setup is not available yet.</h1><a href="/account">Back to my account</a>',503);return true;}
          if(Date.now()-session.created>600_000){send('<h1>Please sign in again before creating your wallet.</h1><p>Sign out, then continue with X again.</p><a href="/account">Back to my account</a>',403);return true;}
          if(form.get('wallet_consent')!=='yes'||[...form.keys()].some(key=>!['csrf','wallet_consent'].includes(key))){send('<h1>Confirm hosted Testnet wallet creation first.</h1><a href="/account">Back to my account</a>',400);return true;}
          try{await provisioner.provision(session.user_id);redirect('/account');}
          catch{send('<h1>Your wallet is not ready yet.</h1><p>Setup is waiting for wallet checks or reconciliation. Existing wallets were preserved. No payment was sent.</p><a class="button" href="/account">Back to my account</a>',503);}
          return true;
        }
        if(path==='/account/settings') {
          if(form.get('enabled')==='yes'&&(!c.hostedWallets||!backend||(await backend.health()).transfersEnabled!==true)){send('<h1>Automatic transfers are not available yet.</h1><p>Your wallet and saved preferences were preserved.</p><a href="/account">Back to my account</a>',400);return true;}
          try {if(zatoshis(form.get('fee')??'')>100_000n)throw new Error('Pilot fee ceiling exceeded.');store.settings(session.user_id,c.network,{amount:form.get('amount')??'',daily:form.get('daily')??'',fee:form.get('fee')??'',enabled:form.get('enabled')==='yes',publicAck:form.get('public_ack')==='yes',consent:form.get('consent')==='yes'},'0.01');}
          catch {send('<h1>Settings were not saved.</h1><p>Enter positive amounts with up to eight decimals. The Testnet pilot allows up to 0.01 per tip and a fee ceiling up to 0.001. Your daily cap must cover the tip plus its fee ceiling. Consent and a ready wallet are required to enable tips.</p><a href="/account">Back to settings</a>',400);return true;}
          redirect('/account');return true;
        }
        if(path==='/account/withdraw-review'||path==='/account/withdraw') {
          const health=backend?await backend.health():undefined;
          if(!c.hostedWallets||c.network!=='testnet'||!health?.transfersEnabled||!health.synced){send('<h1>Withdrawals are waiting for wallet readiness.</h1><a href="/account">Back to my account</a>',503);return true;}
          if(Date.now()-session.created>600_000){send('<h1>Sign in again before withdrawing.</h1><p>Your wallet stays linked. Sign out and continue with X again.</p><a href="/account">Back to my account</a>',403);return true;}
          const allowed=['csrf','form_id','address','amount','fee',...(path==='/account/withdraw'?['confirm']:[])];
          if([...form.keys()].some(key=>!allowed.includes(key)))throw new Error('Invalid withdrawal form.');
          try {
            const address=validateAddress(form.get('address')??'','testnet'),amount=zatoshis(form.get('amount')??''),fee=zatoshis(form.get('fee')??''),id=form.get('form_id')??'';
            if(address.kind!=='unified'||address.transparentReceiver||amount>1_000_000n||fee>100_000n||!/^[a-f0-9]{32}$/.test(id))throw new Error('Invalid withdrawal.');
            if(path==='/account/withdraw-review') {
              send('<section class="composer private-settings"><span class="pill">TESTNET WITHDRAWAL</span><h1>Review your withdrawal</h1><p>Send <strong>'+escape(formatZat(Number(amount)))+' Testnet ZEC</strong>. Maximum additional network fee: <strong>'+escape(formatZat(Number(fee)))+' Testnet ZEC</strong>. Bot service fee: 0.</p><p>Destination — check the full address:</p><p class="address">'+escape(address.address)+'</p><form method="post" action="/account/withdraw"><input type="hidden" name="csrf" value="'+escape(session.csrf)+'"><input type="hidden" name="form_id" value="'+id+'"><input type="hidden" name="address" value="'+escape(address.address)+'"><input type="hidden" name="amount" value="'+escape(form.get('amount')!)+'"><input type="hidden" name="fee" value="'+escape(form.get('fee')!)+'"><p><label><input type="checkbox" name="confirm" value="yes" required> I checked this Testnet address, amount and fee ceiling. Confirming authorizes this withdrawal.</label></p><button class="button" type="submit">Confirm Testnet withdrawal</button></form><p>No transfer is authorized until you confirm. A submitted transaction cannot be undone.</p><a href="/account">Cancel</a></section>');return true;
            }
            if(form.get('confirm')!=='yes')throw new Error('Confirm withdrawal first.');
            store.enqueueWithdrawal(session.user_id,id,address.address,form.get('amount')!,form.get('fee')!);redirect('/account');
          }catch{send('<h1>Withdrawal was not queued.</h1><p>Use a shielded-only Testnet Unified Address. The pilot supports up to 0.01 Testnet ZEC per withdrawal and a fee ceiling up to 0.001. Open a fresh form if this one expired.</p><a href="/account">Back to my account</a>',400);}
          return true;
        }
        res.writeHead(404).end();return true;
      }
      if(path!=='/account'&&path!=='/account/live'){res.writeHead(404).end();return true;}
      const live=path==='/account/live';
      if(live&&((req.headers['sec-fetch-site']&&req.headers['sec-fetch-site']!=='same-origin')||url.search)){res.writeHead(403).end();return true;}
      const p=store.profile(session.user_id,c.network)!, wallet=store.wallet(session.user_id,c.network), csrf=escape(session.csrf), val=(n:number|null)=>n===null?'':formatZat(n);
      let accountHealth:AccountWalletHealth|undefined;
      if(backend){try{accountHealth=await backend.health();}catch{/* Render unavailable; never upstream bodies. */}}
      let balance:WalletBalance|undefined;
      if(wallet?.status==='ready'&&accountHealth?.synced&&backend?.balance){try{balance=await backend.balance(wallet.account);}catch{/* Unavailable is not a zero balance. */}}
      const transfersReady=!!(c.hostedWallets&&accountHealth?.transfersEnabled&&accountHealth.synced);
      let welcomeHtml='';
      if(wallet?.status==='ready'&&accountHealth?.accountAdmissionRequired&&backend?.welcomeStatus){try{
        const grant=await backend.welcomeStatus(wallet.account);
        if(grant.state==='queued')welcomeHtml='<p class="deposit-note" role="status">Your welcome grant is queued.</p>';
        else if(grant.state==='submitted'&&(grant.confirmations===null||grant.confirmations<(grant.minimumConfirmations??10)))welcomeHtml='<p class="deposit-note" role="status">'+(grant.confirmations===null?'Welcome grant sent. Check Activity for status.':'Welcome grant confirming '+grant.confirmations+'/'+(grant.minimumConfirmations??10)+'. Updates automatically.')+'</p>';
        else if(grant.state==='failed'||grant.state==='review')welcomeHtml='<p class="deposit-note" role="status">Your welcome grant needs operator attention. You can still receive Testnet funds at your private address.</p>';
        else if(grant.state==='not_available')welcomeHtml='<p class="deposit-note">Receive Testnet funds to try a tip. Testnet coins have no monetary value.</p>';
      }catch{/* Grant availability must never turn an unavailable balance into zero. */}}

      const setupPanel=c.walletAccounts?'<section class="composer private-settings"><span class="pill">YOUR TESTNET WALLET</span><h2>'+(wallet?.status==='ready'?'Created and linked to your X account':'One account. Your own tipping wallet.')+'</h2><p>'+(wallet?.status==='ready'?'Your wallet mapping is saved. Deposit and transfer checks are still in progress.':'Create a Testnet wallet for this verified X account.')+'</p>'+(wallet?.status==='ready'?'':'<form method="post" action="/account/wallet"><input type="hidden" name="csrf" value="'+csrf+'"><p><label><input type="checkbox" name="wallet_consent" value="yes" required> I agree to create my Testnet wallet.</label></p><button class="button" type="submit" '+(!accountHealth?.accountCreationReady?'disabled':'')+'>Create my Testnet wallet</button></form>')+'<p class="hint">'+(accountHealth?.backupConfirmed?'Operator recovery verified. ':'Wallet checks pending. ')+(accountHealth?.accountCreationReady?'Account creation is ready.':'Waiting for account setup readiness.')+(transfersReady?' Your personal settings control automatic tips.':' Automatic transfers remain disabled.')+'</p></section>':'';
      const withdrawalPanel=!live&&wallet?.status==='ready'&&transfersReady?'<form class="composer private-settings settings-form" method="post" action="/account/withdraw-review"><span class="pill">TESTNET WITHDRAWAL</span><h2>Withdraw</h2><p>Use a shielded-only Testnet address.</p><input type="hidden" name="csrf" value="'+csrf+'"><input type="hidden" name="form_id" value="'+store.withdrawalForm(session.user_id)+'"><label class="field" for="withdraw-address">Testnet address</label><textarea class="address" id="withdraw-address" name="address" required maxlength="2000"></textarea><label class="field" for="withdraw-amount">Amount (TAZ, up to 0.01)</label><input class="account-input" name="amount" id="withdraw-amount" inputmode="decimal" required><label class="field" for="withdraw-fee">Maximum fee (TAZ, up to 0.001)</label><input class="account-input" name="fee" id="withdraw-fee" inputmode="decimal" required value="0.001"><button class="button" type="submit">Review withdrawal</button><p class="hint">Review the address, amount and fee next. A fresh sign-in is required.</p></form>':'';
      const activityItems=store.history(session.user_id,c.network).map(job=>{
        const outgoing=job.sender===session.user_id,other=store.profile(outgoing?job.recipient:job.sender,c.network);
        const who=other?'@'+other.username:'recipient unavailable';
        const time=new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',dateStyle:'medium',timeStyle:'short'}).format(new Date(job.created));
        const status=({queued:'Queued',submitted:'Confirming',confirmed:'Confirmed',failed:'Not sent',review:'Needs review'} as Record<string,string>)[job.status]??job.status;
        const label=store.withdrawal(job.source)?'Withdrawal':(outgoing?'To ':'From ')+who;
        return {created:job.created,html:'<li class="activity-item"><span class="activity-icon">'+glyph(store.withdrawal(job.source)?'wallet':outgoing?'arrow':'down')+'</span><div class="activity-copy"><strong>'+escape(label)+'</strong><small>'+escape(time)+' UTC'+(job.reason?' · '+escape(reasons[job.reason]??'Needs review.'):'')+'</small></div><div class="activity-money"><strong>'+escape(formatZat(job.amount_zat))+' TAZ</strong><span class="state-tag '+(job.status==='confirmed'?'':job.status==='failed'||job.status==='review'?'held':'neutral')+'">'+escape(status)+'</span></div></li>'};
      });
      let depositHistoryUnavailable=false;
      const receiveMinimum=accountHealth?.minimumConfirmations??balance?.minimumConfirmations??10;
      if(wallet?.status==='ready'&&accountHealth?.synced&&backend?.receipts){try{
        const history=await backend.receipts(wallet.account);
        if(history.network!=='testnet'||history.account!==wallet.account)throw new Error('History account mismatch.');
        const shown=store.receiptIdsInHistory(session.user_id,c.network);
        for(const receipt of history.receipts){
          if(shown.has(receipt.receipt))continue;
          const created=receipt.time===null?Date.now():receipt.time*1000;
          const time=receipt.time===null?'Waiting for a block timestamp':new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',dateStyle:'medium',timeStyle:'short'}).format(new Date(created))+' UTC';
          const status=receipt.confirmations>=receiveMinimum?'Received':'Confirming '+receipt.confirmations+'/'+receiveMinimum;
          activityItems.push({created,html:'<li class="activity-item"><span class="activity-icon">'+glyph('down')+'</span><div class="activity-copy"><strong>'+(receipt.confirmations>=receiveMinimum?'Deposit received':'Incoming deposit')+'</strong><small>'+escape(time)+'</small></div><div class="activity-money"><strong>+'+escape(formatZat(receipt.amountZat))+' TAZ</strong><span class="state-tag '+(receipt.confirmations>=receiveMinimum?'':'neutral')+'">'+escape(status)+'</span></div></li>'});
        }
      }catch{depositHistoryUnavailable=true;}}
      const activity=activityItems.sort((a,b)=>b.created-a.created).slice(0,40).map(row=>row.html).join('')+(depositHistoryUnavailable?'<li class="fine-print">Deposit history is temporarily unavailable. The balance above is checked separately.</li>':'');
      const settingsHtml='<form class="composer private-settings settings-form" data-private-preset method="post" action="/account/settings"><h2>Tip settings</h2><input type="hidden" name="csrf" value="'+csrf+'"><div class="settings-fields"><div class="setting-field"><label class="field" for="amount">Default tip (TAZ)</label><input class="account-input" id="amount" name="amount" inputmode="decimal" required value="'+(val(p.default_zat)||'0.001')+'" placeholder="For example, 0.001"></div><div class="setting-field"><label class="field" for="daily">Daily limit, including fees (TAZ)</label><input class="account-input" id="daily" name="daily" inputmode="decimal" required value="'+(val(p.daily_zat)||'0.01')+'"></div><div class="setting-field"><label class="field" for="fee">Maximum network fee (TAZ)</label><input class="account-input" id="fee" name="fee" inputmode="decimal" required value="'+(val(p.fee_zat)||'0.001')+'"></div></div><p class="hint">The wallet calculates the actual network fee. This is only your safety limit; unused allowance is not charged.</p><p><label><input type="checkbox" name="enabled" value="yes" '+(p.enabled&&transfersReady?'checked ':'')+(!transfersReady||wallet?.status!=='ready'?'disabled ':'')+'> Automatic tips</label></p><p><label><input type="checkbox" name="public_ack" value="yes" '+(p.public_ack?'checked':'')+'> Reply under my tip comments (generic acknowledgement)</label></p><p><label><input type="checkbox" name="consent" value="yes" required> I allow this service to send tips when I post a tip command, within these limits.</label></p><button class="button" type="submit">Save settings</button><p class="hint">Applies to new commands. Saving does not send a payment.</p></form>';
      let qr:string|undefined;
      if(!live&&wallet?.status==='ready'&&accountHealth?.depositsEnabled){try{qr=await QRCode.toDataURL(wallet.address,{width:240,margin:4,errorCorrectionLevel:'M',color:{dark:'#131713',light:'#ffffff'}});}catch{}}
      const dailyUsed=(state.db.prepare("SELECT COALESCE(SUM(reserved_zat),0) AS n FROM tip_jobs WHERE sender=? AND network=? AND day=? AND status!='failed'").get(session.user_id,c.network,new Date().toISOString().slice(0,10)) as {n:number}).n;
      const syncHtml=!live&&c.walletHealthFile?walletHealthPanel(await healthReader(c.walletHealthFile)):'';
      const model={profile:p,wallet,health:accountHealth,balance,csrf:session.csrf,qr,settingsHtml,setupHtml:setupPanel,withdrawalHtml:withdrawalPanel,syncHtml,activityHtml:activity,welcomeHtml,dailyUsed,transfersReady,
        liveKey:createHash('sha256').update('zec-tip-bot:live:'+token).digest('hex'),openPanel:(['settings','fund','withdraw'] as const).find(panel=>panel===url.searchParams.get('panel'))};
      if(live){res.setHeader('Content-Type','application/json; charset=utf-8');res.end(JSON.stringify(walletLiveParts(model)));return true;}
      send(privateDashboard(model));return true;
    } catch(error) {
      const status=error instanceof UserAuthError?error.status:undefined;
      // Construct diagnostics from a validated status only. Never render upstream bodies or exception text.
      const detail=status?'X authorization failed (HTTP '+status+'). Check the additional callback URL and existing app permissions.':'Retry from a fresh account page. Existing settings were preserved.';
      send('<h1>Sign-in or account action could not finish.</h1><p>'+detail+'</p><p>Check your private activity before retrying a payment action. Existing wallet records were preserved.</p><a class="button" href="/account">Open account</a>',400);return true;
    }
  };
}




