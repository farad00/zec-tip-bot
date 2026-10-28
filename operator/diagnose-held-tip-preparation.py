#!/usr/bin/env python3
"""One persisted unsigned planning diagnostic, only for a proven pre-signing hold."""
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import urllib.error
STATE=Path('/root/zec-tip-bot-testnet-recovery/held-tip-planning.json')
def load(path,name):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected protected installed diagnostic code.')
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def terms_report(value,request,identity,height):
    outputs=[o for pool in ('sapling','orchard','ironwood') for o in value.get(pool,{}).get('outputs',[])]
    external=[(o.get('user_address'),o.get('value_zat')) for o in outputs if o.get('user_address') is not None]
    return {'transactionVersionMatches':value.get('tx_version')==6,'consensusBranchMatches':value.get('consensus_branch_id')=='77190ad9',
        'fullPrivacyRecorded':value.get('privacy_policy')=='FullPrivacy','walletCreated':value.get('wallet_created') is True,
        'expiryWithinGuard':type(value.get('expiry_height')) is int and height+10<=value['expiry_height']<=height+1000,
        'feeWithinCeiling':type(value.get('fee_zat')) is int and 0<=value['fee_zat']<=request['maxFeeZat'],
        'sourceHintsMatch':value.get('signing_hints')=={'seed_fingerprint':identity['seedfp'],'account_index':identity['zip32_account_index']},
        'transparentInputsAndOutputsAbsent':value.get('transparent')=={'inputs':[],'outputs':[]},
        'externalOutputCount':len(external),'recipientAndAmountMatch':external==[(request['recipient'],request['amountZat'])],
        'missingOutputValues':sum(type(o.get('value_zat')) is not int for o in outputs)}
def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    os.umask(0o077);inspector=load(Path('/root/inspect-held-private-tip.py'),'planning_inspector')
    deposit=load(Path('/root/enable-testnet-deposits.py'),'planning_deposit');module=load(Path('/opt/zec-tip-bot-chain/wallet-transfer-broker.py'),'planning_native')
    result=subprocess.run(['docker','compose','--env-file','.env','-f','deploy/compose.yaml','exec','-T','bot','node','--input-type=module','-e',inspector.READ],cwd=Path('/srv/zec-tip-bot'),check=True,capture_output=True,text=True,timeout=30)
    app=json.loads(result.stdout);key=hashlib.sha256(('zec-tip-bot:testnet:'+app['source']).encode()).hexdigest()
    with closing(sqlite3.connect('file:/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM transfer_jobs WHERE request_key=?',(key,)).fetchone()
    if (app.get('status')!='review' or app.get('proposal') or app.get('signed') or not row or row['phase']!='review'
            or row['pczt'] or row['terms'] or row['signed']):raise ValueError('The payment is not a proven preparation-only hold. It was left untouched.')
    health=deposit.read('/health')
    if health.get('network')!='testnet' or not health.get('synced'):raise ValueError('Wait for synchronized Testnet readiness.')
    request=json.loads(row['request']);rpc=module.NativeRpc();report={'network':'testnet','preSigningHoldVerified':True,'provingSigningOrBroadcastingRequested':False}
    record=None
    if STATE.exists():
        info=STATE.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o077 or info.st_size>4_200_000:raise ValueError('Expected private persisted planning state.')
        record=json.loads(STATE.read_text())
        if record.get('requestKey')!=key:raise ValueError('An earlier different planning diagnostic was preserved.')
        if record.get('phase')=='error':print(json.dumps(record['report'],indent=2));return
        if record.get('phase')!='created':raise ValueError('A previous planning request is uncertain and was not repeated.')
    if record is None:
        deposit.atomic_json(STATE,{'phase':'creating','requestKey':key})
        amount=f"{request['amountZat']//100_000_000}.{request['amountZat']%100_000_000:08d}"
        try:
            raw=rpc.call('pczt_create',[request['account'],[{'address':request['recipient'],'amount':amount}],10,'FullPrivacy'])
        except Exception as error:
            report.update(nativeUnsignedCreationSucceeded=False,nativeErrorCode=getattr(error,'number',None),nativeErrorCategory=type(error).__name__)
            deposit.atomic_json(STATE,{'phase':'error','requestKey':key,'report':report});print(json.dumps(report,indent=2));return
        record={'phase':'created','requestKey':key,'pczt':module.pczt(raw),'returnedPolicy':raw.get('privacy_policy')}
        deposit.atomic_json(STATE,record)
    report.update(nativeUnsignedCreationSucceeded=True,returnedPolicyIsFullPrivacy=record.get('returnedPolicy')=='FullPrivacy')
    value=rpc.call('pczt_inspect',[record['pczt']]);identity=next(r for r in rpc.call('z_listaccounts',[False]) if r.get('account_uuid')==request['account']);height=rpc.call('getblockchaininfo')['blocks']
    report['validationChecks']=terms_report(value,request,identity,height)
    try:module.inspect_terms(value,request,identity,height);report['nativeProposalValidationPassed']=True
    except Exception:report['nativeProposalValidationPassed']=False
    print(json.dumps(report,indent=2))
if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Held planning diagnostic stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
