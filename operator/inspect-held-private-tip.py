#!/usr/bin/env python3
"""Inspect the latest held tip without signing, extracting, broadcasting or exposing terms."""
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
READ='''import {DatabaseSync} from "node:sqlite";if(process.env.ZCASH_NETWORK!=="testnet")process.exit(1);
const db=new DatabaseSync("/app/data/bot.sqlite",{readOnly:true});try{const row=db.prepare("SELECT j.source,j.status,j.reason,s.proposal,s.signed,s.txid FROM tip_jobs j LEFT JOIN tip_spends s ON s.source=j.source WHERE j.network='testnet' AND j.source NOT LIKE 'withdraw-%' ORDER BY j.created DESC LIMIT 1").get();console.log(JSON.stringify(row??null));}finally{db.close();}'''
def load(path,name):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected a protected installed public helper.')
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    result=subprocess.run(['docker','compose','--env-file','.env','-f','deploy/compose.yaml','exec','-T','bot','node','--input-type=module','-e',READ],cwd=Path('/srv/zec-tip-bot'),check=True,capture_output=True,text=True,timeout=30)
    app=json.loads(result.stdout)
    if not isinstance(app,dict):raise ValueError('No private tip record was found.')
    key=hashlib.sha256(('zec-tip-bot:testnet:'+app['source']).encode()).hexdigest()
    path=Path('/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;row=db.execute('SELECT phase,pczt,terms,signed,request FROM transfer_jobs WHERE request_key=?',(key,)).fetchone()
    report={'network':'testnet','inspectionOnly':True,'applicationState':app['status'],
            'applicationReason':app['reason'],'applicationProposalStored':bool(app.get('proposal')),
            'applicationSignedBytesStored':bool(app.get('signed')),'brokerRecordFound':row is not None,
            'signingExtractingOrBroadcastingRequestedByInspector':False}
    if row:
        report.update(brokerPhase=row['phase'],brokerProposalStored=bool(row['pczt']),brokerTermsStored=bool(row['terms']),brokerSignedBytesStored=bool(row['signed']))
        if row['pczt']:
            module=load(Path('/opt/zec-tip-bot-chain/wallet-transfer-broker.py'),'held_tip_native');rpc=module.NativeRpc()
            try:
                value=rpc.call('pczt_inspect',[row['pczt']])
                report['nativeInspectionAvailable']=True
                report['nativeProofs']={pool:value.get(pool,{}).get('proofs_complete' if pool=='sapling' else 'proof_complete') for pool in ('sapling','orchard','ironwood')}
                report['nativeActionCounts']={pool:{field:value.get(pool,{}).get(field) for field in ('actions','signed_actions')} for pool in ('orchard','ironwood')}
                request=json.loads(row['request']);identity=next(r for r in rpc.call('z_listaccounts',[False]) if r.get('account_uuid')==request['account'])
                try:module.inspect_terms(value,request,identity,rpc.call('getblockchaininfo')['blocks']);report['currentTermsValidationPassed']=True
                except Exception:report['currentTermsValidationPassed']=False
            except Exception as error:
                report['nativeInspectionAvailable']=False;report['nativeReadErrorCode']=getattr(error,'number',None)
    print(json.dumps(report,indent=2))
if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Held tip inspection stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
