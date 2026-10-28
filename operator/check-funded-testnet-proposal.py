#!/usr/bin/env python3
"""One native unsigned Testnet proposal, persisted before inspection. Never sign or broadcast."""
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys

STATE=Path('/root/zec-tip-bot-testnet-recovery/unsigned-pilot-proposal.json')
APP=Path('/srv/zec-tip-bot')
READ_BINDINGS='''import {DatabaseSync} from "node:sqlite";
if(process.env.BOT_MODE!=="serve"||process.env.HOSTED_WALLETS_ENABLED!=="false"||process.env.X_POSTING_ENABLED!=="false"||process.env.ZCASH_NETWORK!=="testnet")process.exit(1);
const db=new DatabaseSync("/app/data/bot.sqlite",{readOnly:true});
try{const rows=db.prepare("SELECT w.user_id,p.username,w.account,w.address,w.status FROM tip_wallets w JOIN tip_profiles p ON p.user_id=w.user_id AND p.network=w.network WHERE w.network='testnet' ORDER BY w.account").all();console.log(JSON.stringify(rows));}finally{db.close();}'''


def load(path,name):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected a protected installed public helper.')
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def read_state(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077 or info.st_size>4_200_000:
        raise ValueError('Expected the private persisted unsigned proposal.')
    value=json.loads(path.read_text())
    if not isinstance(value,dict):raise ValueError('Unexpected private proposal state.')
    return value


def bindings():
    result=subprocess.run(['docker','compose','--env-file','.env','-f','deploy/compose.yaml','exec','-T','bot','node','--input-type=module','-e',READ_BINDINGS],cwd=APP,check=True,capture_output=True,text=True,timeout=30)
    rows=json.loads(result.stdout)
    if not isinstance(rows,list) or len(rows)!=2 or any(not isinstance(row,dict) or row.get('status')!='ready' for row in rows):
        raise ValueError('Expected exactly the two existing ready pilot wallet mappings.')
    senders=[row for row in rows if row.get('username')=='farad_0']
    if len(senders)!=1:raise ValueError('Sign in as @farad_0 to confirm the funded pilot account mapping.')
    sender=senders[0];recipient=next(row for row in rows if row['account']!=sender['account'])
    return rows,sender,recipient


def summarize(inspected,request,identity,height,validator):
    passed=False
    try:validator(inspected,request,identity,height);passed=True
    except Exception:pass
    pools={}
    for name in ('sapling','orchard','ironwood'):
        bundle=inspected.get(name,{}) if isinstance(inspected,dict) else {}
        outputs=bundle.get('outputs',[]) if isinstance(bundle,dict) else []
        if not isinstance(outputs,list):outputs=[]
        pools[name]={'outputCount':len(outputs),'missingValueCount':sum(not isinstance(row,dict) or type(row.get('value_zat')) is not int for row in outputs)}
    return {'network':'testnet','fundingSpendable':True,'nativeUnsignedProposalAvailable':True,
            'nativeProposalValidationPassed':passed,
            'nativeTransactionVersion':inspected.get('tx_version') if isinstance(inspected.get('tx_version'),int) else None,
            'consensusBranchMatches':inspected.get('consensus_branch_id')=='77190ad9',
            'fullPrivacyRecorded':inspected.get('privacy_policy')=='FullPrivacy',
            'transparentInputsAndOutputsAbsent':inspected.get('transparent')=={'inputs':[],'outputs':[]},
            'feeWithinPilotCeiling':type(inspected.get('fee_zat')) is int and 0<=inspected['fee_zat']<=request['maxFeeZat'],
            'walletCreated':inspected.get('wallet_created') is True,'outputMetadata':pools,
            'nativeProofRequests':0,'nativeSigningRequests':0,'nativeBroadcastRequests':0,
            'addressesOrAmountsPrinted':False}


def check(rpc,module,deposit,rows,sender,recipient):
    request={'idempotencyKey':hashlib.sha256(b'unsigned-native-pilot-preflight-v1').hexdigest(),
             'account':sender['account'],'recipient':recipient['address'],'amountZat':100_000,'maxFeeZat':100_000}
    fingerprint=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
    if STATE.exists():
        record=read_state(STATE)
        if record.get('requestSha256')!=fingerprint or record.get('phase')!='created':
            raise ValueError('An earlier native planning step is uncertain or differs. It was not repeated.')
    else:
        balance=module.base.read_balance(rpc.call('z_getbalanceforaccount',[sender['account'],10]),sender['account'])
        if balance['confirmedSpendableZat']<500_000:
            return {'network':'testnet','fundingSpendable':False,'nativeProposalRequests':0,
                    'nativeSigningRequests':0,'nativeBroadcastRequests':0,'next':'Wait for the deposit to reach 10 confirmations, then run this same check again.'}
        record={'requestSha256':fingerprint,'phase':'preparing'}
        deposit.atomic_json(STATE,record)
        # Exact decimal string; no memo, source X ID or imported proposal.
        raw=rpc.call('pczt_create',[sender['account'],[{'address':recipient['address'],'amount':'0.00100000'}],10,'FullPrivacy'])
        record.update(phase='created',pczt=module.pczt(raw))
        deposit.atomic_json(STATE,record)
    inspected=rpc.call('pczt_inspect',[record['pczt']])
    if not isinstance(inspected,dict):raise ValueError('Native proposal inspection did not return the expected structure.')
    accounts=rpc.call('z_listaccounts',[False])
    matching=[row for row in accounts if isinstance(row,dict) and row.get('account_uuid')==sender['account']]
    if len(matching)!=1:raise ValueError('Native source account could not be matched privately.')
    height=rpc.call('getblockchaininfo')['blocks']
    return summarize(inspected,request,matching[0],height,module.inspect_terms)


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    os.umask(0o077)
    deposit=load(Path('/root/enable-testnet-deposits.py'),'unsigned_pilot_deposit')
    old=deposit.load_base();pool,recovery,state=old.private_context()
    control=deposit.protected_json(deposit.CONTROL)
    if control.get('network')!='testnet' or control.get('enabled') is not False or control.get('depositsEnabled') is not True:
        raise ValueError('This check requires Testnet deposits enabled and all transfers held.')
    proof=deposit.protected_json(deposit.PROOF);receipt=deposit.protected_json(deposit.RECEIPT,True)
    if control!=deposit.verify_report(proof,receipt) or receipt.get('offServerVerified') is not True:
        raise ValueError('Verified linked recovery is required before native planning.')
    health=deposit.read('/health')
    if not health.get('synced') or not health.get('currentConsensusVerified') or health.get('transfersEnabled') is not False:
        raise ValueError('Wait for synchronized, spending-disabled wallet readiness.')
    rows,sender,recipient=bindings()
    digest=hashlib.sha256('\n'.join(row['account'] for row in rows).encode()).hexdigest()
    if digest!=proof['accountsSha256']:raise ValueError('The application wallet mappings changed after linked backup verification.')
    module=load(Path('/opt/zec-tip-bot-chain/wallet-transfer-broker.py'),'unsigned_native_adapter')
    report=check(module.NativeRpc(),module,deposit,rows,sender,recipient)
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Unsigned Testnet check stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
