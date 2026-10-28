#!/usr/bin/env python3
"""Enable the private Testnet wallet worker with no pending payments or enabled tip profiles."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

APP=Path('/srv/zec-tip-bot')
RECOVERY=Path('/root/zec-tip-bot-testnet-recovery')
ENV=APP/'.env'
CHECK_ENV='''import {parseEnv} from "node:util";
import {readFileSync} from "node:fs";
try{const {before,after}=JSON.parse(readFileSync(0,"utf8")),a=parseEnv(before),b=parseEnv(after);
if(a.HOSTED_WALLETS_ENABLED!=="false"||b.HOSTED_WALLETS_ENABLED!=="true"||Object.keys(a).sort().join("\\n")!==Object.keys(b).sort().join("\\n")||Object.keys(a).some(k=>k!=="HOSTED_WALLETS_ENABLED"&&a[k]!==b[k]))process.exit(1);
}catch{process.exit(1);}'''
GUARD='''if(process.env.BOT_MODE!=="serve"||process.env.PRIVATE_TIPPING_ENABLED!=="true"||process.env.ZCASH_NETWORK!=="testnet"||process.env.X_POSTING_ENABLED!=="false"||process.env.WALLET_ACCOUNTS_ENABLED!=="true"||process.env.WALLET_BROKER_SOCKET!=="/run/zec-tip-bot-wallet-broker/wallet.sock"||process.env.HOSTED_WALLETS_ENABLED!==process.argv[1])process.exit(1);'''
EMPTY='''import {DatabaseSync} from "node:sqlite";
const db=new DatabaseSync("/app/data/bot.sqlite",{readOnly:true});try{
const jobs=db.prepare("SELECT COUNT(*) AS n FROM tip_jobs").get().n;
const enabled=db.prepare("SELECT COUNT(*) AS n FROM tip_profiles WHERE enabled!=0").get().n;
const wallets=db.prepare("SELECT COUNT(*) AS n FROM tip_wallets WHERE network='testnet' AND status='ready'").get().n;
if(jobs!==0||enabled!==0||wallets!==2)process.exit(1);
console.log(JSON.stringify({pendingOrPastPayments:jobs,automaticProfilesEnabled:enabled,readyWallets:wallets}));
}finally{db.close();}'''

def compose(*args,input=None,timeout=120):
    return subprocess.run(['docker','compose','--env-file','.env','-f','deploy/compose.yaml',*args],cwd=APP,
                          check=True,capture_output=True,text=True,input=input,timeout=timeout)

def load(path,name):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected the installed protected pilot helper.')
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def enable_environment(raw):
    text=raw.decode('utf-8')
    pattern=r'(?m)^HOSTED_WALLETS_ENABLED[ \t]*=[^\r\n]*'
    if len(re.findall(pattern,text))!=1:raise ValueError('Expected exactly one explicit hosted-wallet switch in the private environment.')
    return re.sub(pattern,'HOSTED_WALLETS_ENABLED=true',text).encode('utf-8')

def verify_native_report(value):
    flags=('fundingSpendable','nativeUnsignedProposalAvailable','nativeProposalValidationPassed',
           'consensusBranchMatches','fullPrivacyRecorded','transparentInputsAndOutputsAbsent','feeWithinPilotCeiling','walletCreated')
    if (not isinstance(value,dict) or value.get('network')!='testnet' or value.get('nativeTransactionVersion')!=6
            or any(value.get(key) is not True for key in flags)
            or any(value.get(key)!=0 for key in ('nativeProofRequests','nativeSigningRequests','nativeBroadcastRequests'))):
        raise ValueError('The funded native unsigned proposal has not passed all Testnet checks.')

def atomic_environment(path,raw,info):
    temporary=path.with_name('.wallet-pilot-'+uuid.uuid4().hex)
    try:
        with temporary.open('xb') as file:
            os.chmod(temporary,stat.S_IMODE(info.st_mode));os.chown(temporary,info.st_uid,info.st_gid)
            file.write(raw);file.flush();os.fsync(file.fileno())
        os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()

def ready(deposit,enabled):
    h=deposit.read('/health')
    if (h.get('network')!='testnet' or any(h.get(k) is not True for k in ('synced','currentConsensusVerified','backupConfirmed','depositsEnabled'))
            or h.get('transfersEnabled') is not enabled):raise ValueError('Wait for synchronized Testnet wallet readiness. No payment was requested.')
    return h

def activate(deposit):
    info=ENV.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077:raise ValueError('Expected the private root-owned environment file.')
    original=ENV.read_bytes();control=deposit.protected_json(deposit.CONTROL)
    proof=deposit.protected_json(deposit.PROOF);receipt=deposit.protected_json(deposit.RECEIPT,True)
    held=deposit.verify_report(proof,receipt)
    if receipt.get('offServerVerified') is not True:raise ValueError('Off-server linked recovery verification is required.')
    enabled={**held,'enabled':True}
    if control==enabled:
        compose('exec','-T','bot','node','-e',GUARD,'true');ready(deposit,True)
        return {'network':'testnet','withdrawalPilotEnabled':True,'alreadyEnabled':True,'xCommandWorkerEnabled':False,'signingRequestedByHelper':False}
    if control!=held:raise ValueError('Different operator controls were preserved. Activation was not repeated.')
    ready(deposit,False);compose('exec','-T','bot','node','-e',GUARD,'false')
    counts=json.loads(compose('exec','-T','bot','node','--input-type=module','-e',EMPTY).stdout)
    if counts!={'pendingOrPastPayments':0,'automaticProfilesEnabled':0,'readyWallets':2}:raise ValueError('Pilot accounts must have no existing payment jobs or enabled automatic tips.')
    config=json.loads((APP/'deploy/compose.yaml').read_text())
    if 'HOSTED_WALLETS_ENABLED' in config.get('services',{}).get('bot',{}).get('environment',{}):
        raise ValueError('A separate Compose hosted-wallet override was preserved.')
    result=subprocess.run(['/usr/bin/python3','/root/check-funded-testnet-proposal.py'],check=True,capture_output=True,text=True,timeout=240)
    verify_native_report(json.loads(result.stdout))
    after=enable_environment(original)
    compose('exec','-T','bot','node','--input-type=module','-e',CHECK_ENV,
            input=json.dumps({'before':original.decode('utf-8'),'after':after.decode('utf-8')}))
    group=deposit.CONTROL.stat().st_gid
    backup=RECOVERY/('withdrawal-pilot-'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
    (backup/'previous.env').write_bytes(original);(backup/'previous.env').chmod(0o600)
    deposit.atomic_json(backup/'previous-controls.json',control)
    stopped=False
    try:
        compose('stop','bot');stopped=True
        atomic_environment(ENV,after,info)
        deposit.atomic_json(deposit.CONTROL,enabled,group)
        ready(deposit,True)
        compose('up','-d','--no-deps','--force-recreate','bot')
        compose('exec','-T','bot','node','-e',GUARD,'true')
        for attempt in range(15):
            try:
                compose('exec','-T','bot','node','--input-type=module','-e','const r=await fetch("http://127.0.0.1:3001/healthz");if(!r.ok)process.exit(1);')
                break
            except subprocess.SubprocessError:
                if attempt==14:raise
                time.sleep(2)
        ready(deposit,True)
    except BaseException:
        deposit.atomic_json(deposit.CONTROL,held,group)
        atomic_environment(ENV,original,info)
        if stopped:compose('up','-d','--no-deps','--force-recreate','bot')
        raise
    return {'network':'testnet','withdrawalPilotEnabled':True,'depositsEnabled':True,
            'privateWalletWorkerEnabled':True,'xCommandWorkerEnabled':False,'postingEnabled':False,
            'automaticProfilesEnabled':0,'signingRequestedByHelper':False,'broadcastRequestedByHelper':False}

def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    import fcntl
    os.umask(0o077)
    with open('/run/zec-tip-bot-withdrawal-pilot.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        deposit=load(Path('/root/enable-testnet-deposits.py'),'withdrawal_deposit')
        print(json.dumps(activate(deposit),indent=2))
    print('Sign in again as @farad_0. Withdraw requires an explicit review and confirmation. No payment was requested by this helper.')

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Withdrawal pilot stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
