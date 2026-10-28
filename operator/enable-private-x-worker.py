#!/usr/bin/env python3
"""Start private Testnet comment processing only after a real confirmed withdrawal."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

GUARD='''if(process.env.BOT_MODE!==process.argv[1]||process.env.X_POSTING_ENABLED!==process.argv[2]||process.env.PRIVATE_TIPPING_ENABLED!=="true"||process.env.ZCASH_NETWORK!=="testnet"||process.env.HOSTED_WALLETS_ENABLED!=="true"||process.env.WALLET_ACCOUNTS_ENABLED!=="true")process.exit(1);'''
MODE='''if(process.env.BOT_MODE==="start"&&process.env.X_POSTING_ENABLED==="true")console.log("active");else if(process.env.BOT_MODE==="serve"&&process.env.X_POSTING_ENABLED==="false")console.log("held");else process.exit(1);'''
CHECK_ENV='''import {parseEnv} from "node:util";import {readFileSync} from "node:fs";
try{const {before,after}=JSON.parse(readFileSync(0,"utf8")),a=parseEnv(before),b=parseEnv(after),changed=["BOT_MODE","X_POSTING_ENABLED"];
if(a.BOT_MODE!=="serve"||b.BOT_MODE!=="start"||a.X_POSTING_ENABLED!=="false"||b.X_POSTING_ENABLED!=="true"||Object.keys(a).sort().join("\\n")!==Object.keys(b).sort().join("\\n")||Object.keys(a).some(k=>!changed.includes(k)&&a[k]!==b[k]))process.exit(1);
}catch{process.exit(1);}'''
PRECHECK='''import {DatabaseSync} from "node:sqlite";
const db=new DatabaseSync("/app/data/bot.sqlite",{readOnly:true});try{
const profilesEnabled=db.prepare("SELECT COUNT(*) AS n FROM tip_profiles WHERE enabled!=0").get().n;
const readyWallets=db.prepare("SELECT COUNT(*) AS n FROM tip_wallets WHERE network='testnet' AND status='ready'").get().n;
const pendingJobs=db.prepare("SELECT COUNT(*) AS n FROM tip_jobs WHERE status NOT IN ('confirmed','failed')").get().n;
const workerHeld=!!db.prepare("SELECT value FROM settings WHERE key='worker_hold'").get()?.value;
const payment=db.prepare("SELECT s.txid FROM tip_jobs j JOIN tip_withdrawals w ON w.source=j.source JOIN tip_spends s ON s.source=j.source JOIN tip_profiles p ON p.user_id=j.sender AND p.network=j.network WHERE j.network='testnet' AND p.username='farad_0' AND j.status='confirmed' ORDER BY j.created DESC LIMIT 1").get();
console.log(JSON.stringify({profilesEnabled,readyWallets,pendingJobs,workerHeld,txid:payment?.txid}));
}finally{db.close();}'''
VERIFY='''import {loadConfig} from "./dist/src/config.js";import {State} from "./dist/src/state.js";import {XApi} from "./dist/src/x.js";
const c=loadConfig(),state=new State("/app/data/bot.sqlite");try{const api=new XApi(c,cost=>state.reserveRequest(cost,c.budgetMills));await api.me();console.log(JSON.stringify({verifiedBot:"@zectipbot",postsMade:0}));}catch{process.exitCode=1;}finally{state.close();}'''
CURSOR='''import {State} from "./dist/src/state.js";import {currentSnowflake} from "./dist/src/bot.js";
const state=new State("/app/data/bot.sqlite");let leased=false;
try{state.acquire("bot");leased=true;
if(state.get("worker_hold")||state.db.prepare("SELECT COUNT(*) AS n FROM tip_profiles WHERE enabled!=0").get().n!==0)process.exitCode=1;
else{const first=!state.get("private_x_activation_v1");if(first){state.db.exec("BEGIN IMMEDIATE");try{state.set("since_id",currentSnowflake());state.set("private_x_activation_v1",new Date().toISOString());state.db.exec("COMMIT");}catch(e){state.db.exec("ROLLBACK");throw e;}}
console.log(JSON.stringify({commandWindowInitialized:true,historicalCommandsSkipped:first,existingJobsAndDedupeRecordsPreserved:true}));}
}finally{if(leased)state.release("bot");state.close();}'''
CHECK_WORKER='''import {DatabaseSync} from "node:sqlite";
const db=new DatabaseSync("/app/data/bot.sqlite",{readOnly:true});try{
if(db.prepare("SELECT value FROM settings WHERE key='worker_hold'").get()?.value)process.exit(1);
if(!db.prepare("SELECT value FROM settings WHERE key='private_x_activation_v1'").get()?.value)process.exit(1);
}finally{db.close();}'''

def enable_environment(raw):
    text=raw.decode('utf-8')
    for key,value in (('BOT_MODE','start'),('X_POSTING_ENABLED','true')):
        pattern=r'(?m)^'+key+r'[ \t]*=[^\r\n]*'
        if len(re.findall(pattern,text))!=1:raise ValueError('Expected one explicit private worker switch: '+key)
        text=re.sub(pattern,key+'='+value,text)
    return text.encode('utf-8')

def verify_confirmation(value):
    if not isinstance(value,dict) or type(value.get('confirmations')) is not int or value['confirmations']<3:
        raise ValueError('Wait for the first withdrawal to have at least three chain confirmations.')

def activate(base,deposit):
    info=base.ENV.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077:raise ValueError('Expected the private root-owned environment.')
    original=base.ENV.read_bytes();base.ready(deposit,True)
    mode=base.compose('exec','-T','bot','node','-e',MODE).stdout.strip()
    if mode=='active':
        base.compose('exec','-T','bot','node','-e',GUARD,'start','true')
        base.compose('exec','-T','bot','node','--input-type=module','-e',CHECK_WORKER)
        return {'network':'testnet','automaticXWorkerEnabled':True,'alreadyEnabled':True,'commandCursorReset':False,'signingRequestedByHelper':False}
    if mode!='held':raise ValueError('Unexpected worker mode. Existing configuration was preserved.')
    base.compose('exec','-T','bot','node','-e',GUARD,'serve','false')
    pending=json.loads(base.compose('exec','-T','bot','node','--input-type=module','-e',PRECHECK).stdout)
    if pending.get('profilesEnabled')!=0:raise ValueError('Pause automatic tips in both accounts before activating the X worker.')
    if pending.get('readyWallets')!=2:raise ValueError('Both backed-up pilot wallets must be ready.')
    if pending.get('pendingJobs')!=0:raise ValueError('Wait for pending wallet payments to finish before activating X processing.')
    if pending.get('workerHeld') is not False:raise ValueError('An existing X worker error hold requires attention; it was preserved.')
    txid=pending.get('txid')
    if not isinstance(txid,str) or not re.fullmatch('[a-f0-9]{64}',txid):raise ValueError('A confirmed native withdrawal is required before X processing.')
    verify_confirmation(deposit.read('/confirmations/'+txid))
    config=json.loads((base.APP/'deploy/compose.yaml').read_text())
    if any(key in config.get('services',{}).get('bot',{}).get('environment',{}) for key in ('BOT_MODE','X_POSTING_ENABLED')):
        raise ValueError('Separate Compose worker overrides were preserved.')
    after=enable_environment(original)
    base.compose('exec','-T','bot','node','--input-type=module','-e',CHECK_ENV,
                 input=json.dumps({'before':original.decode(),'after':after.decode()}))
    # One paid identity read. No mentions, media uploads or posts in this step.
    verified=json.loads(base.compose('exec','-T','bot','node','--input-type=module','-e',VERIFY).stdout)
    if verified!={'verifiedBot':'@zectipbot','postsMade':0}:raise ValueError('The authenticated bot identity was not verified.')
    backup=base.RECOVERY/('x-worker-'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
    (backup/'previous.env').write_bytes(original);(backup/'previous.env').chmod(0o600)
    stopped=False
    try:
        base.compose('stop','bot');stopped=True
        cursor=json.loads(base.compose('run','--rm','--no-deps','bot','node','--input-type=module','-e',CURSOR).stdout)
        if cursor.get('commandWindowInitialized') is not True:raise ValueError('A fresh private command window could not be established.')
        base.atomic_environment(base.ENV,after,info)
        base.compose('up','-d','--no-deps','--force-recreate','bot')
        base.compose('exec','-T','bot','node','-e',GUARD,'start','true')
        for _ in range(3):
            time.sleep(3)
            base.compose('exec','-T','bot','node','--input-type=module','-e',CHECK_WORKER)
    except BaseException:
        base.atomic_environment(base.ENV,original,info)
        if stopped:base.compose('up','-d','--no-deps','--force-recreate','bot')
        # Keep cursor/dedupe records; restoring an older cursor could replay public commands.
        raise
    return {'network':'testnet','verifiedBot':'@zectipbot','automaticXWorkerEnabled':True,
            'automaticProfilesEnabled':0,'privateWalletWorkerEnabled':True,
            'historicalCommandsSkipped':cursor.get('historicalCommandsSkipped') is True,
            'financialRepliesEnabled':False,'commentTipDeliveryStillToVerify':True,
            'signingRequestedByHelper':False,'xApiCreditsUsed':True}

def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    import importlib.util,fcntl
    path=Path('/root/enable-testnet-withdrawal-pilot.py');info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected the installed withdrawal pilot helper.')
    spec=importlib.util.spec_from_file_location('x_worker_base',path);base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
    os.umask(0o077)
    with open('/run/zec-tip-bot-withdrawal-pilot.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        deposit=base.load(Path('/root/enable-testnet-deposits.py'),'x_worker_deposit')
        print(json.dumps(activate(base,deposit),indent=2))
    print('Enable a private preset, then post one new tip command. Existing posts were not used to authorize a payment.')

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Private X worker stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
