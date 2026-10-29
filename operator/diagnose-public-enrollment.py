#!/usr/bin/env python3
"""Read-only signup-stage checks. No account/address creation or payment calls."""
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys

QUERY='''import {DatabaseSync} from "node:sqlite";const db=new DatabaseSync("/app/data/bot.sqlite",{readOnly:true});try{console.log(JSON.stringify(db.prepare("SELECT user_id,label,phase,account,reason FROM wallet_provisions ORDER BY rowid DESC LIMIT 1").get()??{}));}finally{db.close();}'''


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Use the VPS root terminal.')
    result=subprocess.run(['docker','compose','--env-file','.env','-f','deploy/compose.yaml','exec','-T','bot','node','--input-type=module','-e',QUERY],
                          cwd='/srv/zec-tip-bot',check=True,capture_output=True,text=True)
    row=json.loads(result.stdout)
    report={'network':'testnet','readOnly':True,'applicationProvisionFound':bool(row),
            'applicationPhase':row.get('phase'),'applicationReason':row.get('reason'),
            'newAccountsAddressesOrPaymentsRequested':False}
    file=Path('/opt/zec-tip-bot-chain/wallet-transfer-broker.py');info=file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected the protected native adapter.')
    spec=importlib.util.spec_from_file_location('signup_readonly_native',file);native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
    accounts=native.NativeRpc().call('z_listaccounts',[False])
    control=json.loads(Path('/etc/zec-tip-bot-wallet-transfers.json').read_text())
    baseline=control.get('enrollment',{}).get('baselineAccounts',[])
    matched=[r for r in accounts if r.get('name')==row.get('label')]
    seeds={r.get('seedfp') for r in accounts if r.get('account_uuid') in baseline}
    report.update(nativeAccounts=len(accounts),nativeOpaqueLabelMatches=len(matched),
        nativeAccountMatchesApplication=bool(len(matched)==1 and matched[0].get('account_uuid')==row.get('account')),
        nativeSeedMatchesBaseline=bool(len(matched)==1 and seeds=={matched[0].get('seedfp')}),
        nativeAccountIndexPresent=bool(len(matched)==1 and type(matched[0].get('zip32_account_index')) is int))
    with closing(sqlite3.connect('file:/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite?mode=ro',uri=True)) as db:
        intent=db.execute('SELECT phase,account FROM account_intents WHERE label=?',(row.get('label'),)).fetchone()
        binding=db.execute('SELECT phase FROM enrollment_bindings WHERE user_id=?',(row.get('user_id'),)).fetchone()
    report.update(brokerIntentPhase=intent[0] if intent else None,
        brokerBindingPhase=binding[0] if binding else None,
        identitiesAddressesOrKeysPrinted=False)
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:print('Signup diagnostic unavailable: '+type(error).__name__);raise SystemExit(1)
