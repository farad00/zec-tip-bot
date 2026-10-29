#!/usr/bin/env python3
"""Read-only aggregate enrollment/grant checks, without account identifiers."""
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the VPS root terminal.')
    path=Path('/root/enable-testnet-deposits.py');info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected the protected readiness helper.')
    spec=importlib.util.spec_from_file_location('enrollment_check_health',path);helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    health=helper.read('/health')
    with closing(sqlite3.connect('file:/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite?mode=ro',uri=True)) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        admissions=dict(db.execute('SELECT phase,COUNT(*) FROM enrollment_bindings GROUP BY phase')) if 'enrollment_bindings' in tables else {}
        grants=dict(db.execute('SELECT phase,COUNT(*) FROM welcome_grants GROUP BY phase')) if 'welcome_grants' in tables else {}
        rows=db.execute('SELECT request_key FROM welcome_grants WHERE phase=\'submitted\'').fetchall() if 'welcome_grants' in tables else []
        counts=[]
        for key, in rows:
            signed=db.execute('SELECT signed FROM transfer_jobs WHERE request_key=?',(key,)).fetchone()
            if signed and signed[0]:
                result=helper.read('/confirmations/'+json.loads(signed[0])['txid']);counts.append(result['confirmations'])
    print(json.dumps({'network':'testnet','readOnly':True,'newAccountsReady':health.get('accountCreationReady') is True,
        'transfersReady':health.get('transfersEnabled') is True,'enrollments':admissions,'welcomeGrants':grants,
        'welcomeGrantConfirmations':counts,'newSnapshotsNeedOffServerCopy':bool(admissions),
        'identitiesAddressesAmountsOrTransactionIdsPrinted':False},indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:print('Enrollment check unavailable: '+type(error).__name__);raise SystemExit(1)
