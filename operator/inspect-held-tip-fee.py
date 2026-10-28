#!/usr/bin/env python3
"""Compare the existing unsigned proposal fee against saved and pilot limits without printing values."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    import importlib.util
    path=Path('/root/diagnose-held-tip-preparation.py');spec=importlib.util.spec_from_file_location('fee_base',path);base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
    module=base.load(Path('/opt/zec-tip-bot-chain/wallet-transfer-broker.py'),'fee_native')
    record=json.loads(base.STATE.read_text())
    if record.get('phase')!='created':raise ValueError('Expected the existing unsigned diagnostic proposal.')
    with closing(sqlite3.connect('file:/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite?mode=ro',uri=True)) as db:
        row=db.execute('SELECT request FROM transfer_jobs WHERE request_key=?',(record['requestKey'],)).fetchone()
    if not row:raise ValueError('Expected the held tip request.')
    request=json.loads(row[0]);value=module.NativeRpc().call('pczt_inspect',[record['pczt']]);fee=value.get('fee_zat')
    print(json.dumps({'network':'testnet','inspectionOnly':True,'feeIsInteger':type(fee) is int,
        'feeNonnegative':type(fee) is int and fee>=0,'requiredFeeWithinPilotMaximum':type(fee) is int and 0<=fee<=module.MAX_FEE,
        'savedFeeCeilingEqualsPilotMaximum':request['maxFeeZat']==module.MAX_FEE,
        'requiredFeeExceedsSavedCeiling':type(fee) is int and fee>request['maxFeeZat'],
        'settingsOrPaymentsChanged':False},indent=2))
if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Fee inspection stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
