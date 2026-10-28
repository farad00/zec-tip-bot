#!/usr/bin/env python3
"""Read the latest refused tip's persisted proposal and compare limits privately."""
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

READ='''import {DatabaseSync} from "node:sqlite";if(process.env.ZCASH_NETWORK!=="testnet")process.exit(1);
const db=new DatabaseSync("/app/data/bot.sqlite",{readOnly:true});try{console.log(JSON.stringify(db.prepare("SELECT j.source,j.status,j.reason,j.fee_zat,j.created,p.fee_zat AS saved_fee,p.enabled_after FROM tip_jobs j JOIN tip_profiles p ON p.user_id=j.sender AND p.network=j.network WHERE j.network='testnet' AND j.source NOT LIKE 'withdraw-%' ORDER BY j.created DESC LIMIT 1").get()??null));}finally{db.close();}'''

def comparisons(app,request,fee,maximum):
    return {'network':'testnet','readOnly':True,
        'currentSavedMaximumIsPilotMaximum':app['saved_fee']==maximum,
        'acceptedCommandMaximumIsPilotMaximum':app['fee_zat']==maximum,
        'acceptedMaximumMatchesCurrentSettings':app['fee_zat']==app['saved_fee'],
        'brokerMaximumMatchesAcceptedCommand':request['maxFeeZat']==app['fee_zat'],
        'settingsWereSavedBeforeCommand':app['enabled_after']<=app['created'],
        'calculatedFeeWithinPilotMaximum':0<=fee<=maximum,
        'calculatedFeeExceedsAcceptedMaximum':fee>request['maxFeeZat'],
        'signingOrBroadcastingRequested':False,
        'identitiesAddressesAmountsOrTransactionIdsPrinted':False}

def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the VPS root terminal.')
    path=Path('/root/update-visible-settings.py')
    spec=importlib.util.spec_from_file_location('latest_fee_loader',path)
    loader=importlib.util.module_from_spec(spec);spec.loader.exec_module(loader)
    native=loader.load(Path('/opt/zec-tip-bot-chain/wallet-transfer-broker.py'),'latest_fee_native')
    result=subprocess.run(['docker','compose','--env-file','.env','-f','deploy/compose.yaml','exec','-T','bot','node','--input-type=module','-e',READ],
        cwd='/srv/zec-tip-bot',check=True,capture_output=True,text=True,timeout=30)
    app=json.loads(result.stdout)
    if not app or app['status']!='failed' or app['reason']!='network_fee_limit':raise ValueError('The latest tip is not a definite fee refusal; it was left unchanged.')
    key=hashlib.sha256(('zec-tip-bot:testnet:'+app['source']).encode()).hexdigest()
    with closing(sqlite3.connect('file:/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        row=db.execute('SELECT phase,pczt,signed,request FROM transfer_jobs WHERE request_key=?',(key,)).fetchone()
    if not row or row['phase']!='rejected' or row['signed'] or not row['pczt']:raise ValueError('Expected the persisted unsigned refused proposal. No new proposal was requested.')
    value=native.NativeRpc().call('pczt_inspect',[row['pczt']]);fee=value.get('fee_zat')
    if type(fee) is not int:raise ValueError('The fee inspection did not return an integer.')
    print(json.dumps(comparisons(app,json.loads(row['request']),fee,native.MAX_FEE),indent=2))

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Latest fee inspection stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
