#!/usr/bin/env python3
"""Show settings below the wallet while preserving the currently running workers and controls."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
ARCHIVE=Path('/root/zec-visible-settings.zip')
ARCHIVE_SHA='5ca8f865b383e4dae8cbaa5c3565870ee0eaf325928409601cc0fe43642c31eb'
BASE_SHA='e4610816c179bc468e7023a26c537431ba3c177a5c9d83fd6aac6c7b8adf89e4'
FILES={'src/account.ts','src/account-ui.ts','test/account-ui.test.ts','README.md','START-HERE.md','PRIVATE-TIPPING.md','LICENSE','package.json','package-lock.json','.env.example','deploy/README.md','deploy/WALLET-BACKEND.md','deploy/host.env.example'}
RUNTIME='''const p=process.env;if(!["serve","start"].includes(p.BOT_MODE)||!["true","false"].includes(p.HOSTED_WALLETS_ENABLED)||p.PRIVATE_TIPPING_ENABLED!=="true"||p.ZCASH_NETWORK!=="testnet"||p.X_POSTING_ENABLED!==(p.BOT_MODE==="start"?"true":"false"))process.exit(1);console.log(JSON.stringify({BOT_MODE:p.BOT_MODE,HOSTED_WALLETS_ENABLED:p.HOSTED_WALLETS_ENABLED,X_POSTING_ENABLED:p.X_POSTING_ENABLED}));'''
IDLE='''import {DatabaseSync} from "node:sqlite";const db=new DatabaseSync("/app/data/bot.sqlite",{readOnly:true});try{if(db.prepare("SELECT COUNT(*) AS n FROM tip_jobs WHERE status IN ('queued','preparing','prepared','signing','signed','broadcasting','submitted')").get().n!==0)process.exit(1);}finally{db.close();}'''

def load(path,name,sha=None):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or (sha and hashlib.sha256(path.read_bytes()).hexdigest()!=sha):raise ValueError('Expected the verified installed public update helper.')
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def install(base,deposit):
    original_compose=base.compose
    runtime=json.loads(original_compose('exec','-T','bot','node','-e',RUNTIME).stdout)
    if set(runtime)!={'BOT_MODE','HOSTED_WALLETS_ENABLED','X_POSTING_ENABLED'}:raise ValueError('Unexpected runtime metadata.')
    base.PREFLIGHT='if('+ '||'.join('process.env.'+key+'!=='+json.dumps(value) for key,value in sorted(runtime.items()))+'||process.env.PRIVATE_TIPPING_ENABLED!=="true"||process.env.ZCASH_NETWORK!=="testnet")process.exit(1);'
    def health():
        value=deposit.read('/health')
        if value.get('network')!='testnet':raise ValueError('Only the existing Testnet deployment is supported.')
        return value
    before=health()
    def compose(*args,**kwargs):
        result=original_compose(*args,**kwargs)
        # Fail before the base installer reaches its restart phase, leaving active payments alone.
        if args[:1]==('run',):original_compose('exec','-T','bot','node','--input-type=module','-e',IDLE)
        return result
    original_compose('exec','-T','bot','node','--input-type=module','-e',IDLE)
    base.compose=compose;base.broker_health=health;base.ARCHIVE_SHA=ARCHIVE_SHA;base.FILES=FILES
    report=base.install(base.payload(ARCHIVE.read_bytes()))
    after=health()
    if after.get('transfersEnabled')!=before.get('transfersEnabled'):raise ValueError('Transfer availability changed during the website update; inspect readiness.')
    report.update(settingsVisibleBelowWallet=True,transfersEnabled=after.get('transfersEnabled') is True,
                  automaticXWorkerEnabled=runtime['BOT_MODE']=='start',workerConfigurationPreserved=True)
    return report

def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    os.umask(0o077)
    base=load(Path('/root/update-dashboard.py'),'visible_settings_base',BASE_SHA)
    deposit=load(Path('/root/enable-testnet-deposits.py'),'visible_settings_health')
    print(json.dumps(install(base,deposit),indent=2))
    print('Refresh the account page. Tip settings are visible immediately below the wallet.')
if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Visible settings update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
