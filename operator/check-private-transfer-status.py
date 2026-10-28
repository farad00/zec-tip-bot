#!/usr/bin/env python3
"""Read private pilot payment state, returning no identities, addresses, amounts or transaction IDs."""
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
APP=Path('/srv/zec-tip-bot')
READ='''import {DatabaseSync} from "node:sqlite";
if(process.env.ZCASH_NETWORK!=="testnet"||process.env.PRIVATE_TIPPING_ENABLED!=="true")process.exit(1);
const db=new DatabaseSync("/app/data/bot.sqlite",{readOnly:true});try{
const jobs=db.prepare("SELECT j.status,j.reason,s.txid,w.source IS NOT NULL AS withdrawal FROM tip_jobs j LEFT JOIN tip_spends s ON s.source=j.source LEFT JOIN tip_withdrawals w ON w.source=j.source WHERE j.network='testnet' ORDER BY j.created DESC LIMIT 5").all();console.log(JSON.stringify(jobs));
}finally{db.close();}'''
def summarize(rows,read):
    reports=[]
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Unexpected private payment state.')
        report={'kind':'withdrawal' if row.get('withdrawal') else 'tip',
                'state':row.get('status') if row.get('status') in {'queued','preparing','prepared','signing','signed','broadcasting','submitted','confirmed','failed','review'} else 'unknown'}
        reason=row.get('reason')
        if reason and report['state']=='review':report['needsReview']=True
        elif reason and report['state']=='failed':
            allowed={'network_fee_limit','wallet_rejected','setup_required','wallet_unavailable','stale_command','daily_limit','tips_paused','self_tip','mainnet_disabled'}
            report['failureCategory']=reason if reason in allowed else 'not_sent'
        txid=row.get('txid')
        if isinstance(txid,str) and re.fullmatch('[a-f0-9]{64}',txid):
            try:
                confirmations=read('/confirmations/'+txid).get('confirmations')
                if type(confirmations) is not int or confirmations<0:raise ValueError('Unexpected confirmation count')
                report['chainConfirmations']=confirmations
            except Exception:report['chainConfirmationRead']='unavailable'
        reports.append(report)
    return {'network':'testnet','readOnly':True,'recentPayments':reports,'identitiesAddressesAmountsOrTransactionIdsPrinted':False}
def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    path=Path('/root/enable-testnet-deposits.py');info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected protected installed readiness helper.')
    spec=importlib.util.spec_from_file_location('private_status',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result=subprocess.run(['docker','compose','--env-file','.env','-f','deploy/compose.yaml','exec','-T','bot','node','--input-type=module','-e',READ],cwd=APP,check=True,capture_output=True,text=True,timeout=30)
    rows=json.loads(result.stdout)
    if not isinstance(rows,list) or len(rows)>5:raise ValueError('Unexpected private payment report.')
    print(json.dumps(summarize(rows,module.read),indent=2))
if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Private transfer check stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
