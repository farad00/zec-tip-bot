// Serialized into the same-origin browser bundle. Never store wallet data in browser storage.
export function installWalletLive(win:Window=window,doc:Document=document) {
  const selected=doc.querySelector<HTMLElement>('[data-live-key]');
  if(!selected?.dataset.liveKey)return;
  const page=selected;
  const key=page.dataset.liveKey;
  let timer:number|undefined,controller:AbortController|undefined,stopped=false,failures=0,busy=false;
  const status=()=>page.querySelector<HTMLElement>('[data-live-status]');
  const text=(selector:string,value:string)=>{const el=page.querySelector(selector);if(el)el.textContent=value;};
  const html=(selector:string,value:string)=>{const el=page.querySelector(selector);if(el)el.innerHTML=value;};
  const stop=(message:string)=>{
    stopped=true;win.clearTimeout(timer);controller?.abort();
    // Clear every private field, including addresses and forms, on session expiry/change.
    const box=doc.createElement('div'),note=doc.createElement('p'),link=doc.createElement('a');
    note.textContent=message;link.textContent='Sign in again';link.href='/account';link.className='button';
    box.append(note,link);page.replaceChildren(box);
  };
  const schedule=()=>{win.clearTimeout(timer);if(!stopped&&!doc.hidden)timer=win.setTimeout(update,Math.min(60000,15000*2**failures));};
  async function update() {
    if(stopped||busy||doc.hidden)return;
    busy=true;controller=new AbortController();const abort=win.setTimeout(()=>controller?.abort(),12000);
    try {
      const response=await win.fetch('/account/live',{credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal});
      if(response.status===401){stop('Your session expired. Sign in again to see your wallet.');return;}
      if(!response.ok||!response.headers.get('content-type')?.startsWith('application/json'))throw Error('Unavailable');
      const raw=await response.text();if(raw.length>160000)throw Error('Invalid update');
      const data=JSON.parse(raw);
      if(!data||typeof data.liveKey!=='string')throw Error('Invalid update');
      if(data.liveKey!==key){stop('Your sign-in changed. Open your wallet again.');return;}
      for(const name of ['balance','balanceHint','pendingHtml','welcomeHtml','activityHtml','walletStatus','dailyValues','tipWarningHtml'])
        if(typeof data[name]!=='string'||data[name].length>120000)throw Error('Invalid update');
      if(typeof data.dailyPercent!=='number'||!Number.isFinite(data.dailyPercent)||data.dailyPercent<0||data.dailyPercent>100)throw Error('Invalid update');
      const value=page.querySelector<HTMLElement>('[data-balance-value]'),eye=page.querySelector('[data-balance-toggle]');
      if(value){value.dataset.balanceValue=data.balance;value.textContent=eye?.getAttribute('aria-pressed')==='true'?'••••••':data.balance;}
      text('[data-live-balance-hint]',data.balanceHint);text('[data-live-wallet-state]',data.walletStatus);
      text('[data-live-daily-values]',data.dailyValues);
      html('[data-live-pending]',data.pendingHtml);html('[data-live-welcome]',data.welcomeHtml);
      html('[data-live-activity]',data.activityHtml);html('[data-live-tip-warning]',data.tipWarningHtml);
      const meter=page.querySelector<HTMLElement>('.budget-meter'),bar=meter?.querySelector<HTMLElement>('span');
      meter?.setAttribute('aria-valuenow',String(data.dailyPercent));if(bar)bar.style.width=data.dailyPercent+'%';
      failures=0;if(status())status()!.textContent='Auto-updates on';
      // Deliberately leave all form inputs, disclosure panels and scroll position untouched.
    }catch{if(!stopped&&!doc.hidden){failures=Math.min(2,failures+1);if(status())status()!.textContent='Reconnecting…';}}
    finally{win.clearTimeout(abort);busy=false;controller=undefined;schedule();}
  }
  doc.addEventListener('visibilitychange',()=>{win.clearTimeout(timer);if(doc.hidden)controller?.abort();else void update();});
  win.addEventListener('pagehide',()=>{stopped=true;win.clearTimeout(timer);controller?.abort();});
  win.addEventListener('pageshow',event=>{if(event.persisted&&page.querySelector('[data-live-status]')){stopped=false;void update();}});
  schedule();
}
export const LIVE_JS=';('+installWalletLive.toString()+')();';
