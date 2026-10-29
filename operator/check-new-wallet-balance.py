#!/usr/bin/env python3
"""Read-only confirmation and pending-balance flags for the latest admitted account."""
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Use the VPS root terminal.')
    file=Path('/opt/zec-tip-bot-chain/wallet-transfer-broker.py');info=file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected the protected native adapter.')
    spec=importlib.util.spec_from_file_location('private_balance_check',file);native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
    rpc=native.NativeRpc();rpc.ALLOWED=rpc.ALLOWED|{'z_getbalances'}
    with closing(sqlite3.connect('file:/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite?mode=ro',uri=True)) as db:
        row=db.execute("SELECT account FROM enrollment_bindings WHERE phase='active' ORDER BY rowid DESC LIMIT 1").fetchone()
        if not row:raise ValueError('No newly admitted wallet.')
        account=row[0]
        grants=db.execute('SELECT t.signed FROM welcome_grants w JOIN transfer_jobs t ON t.request_key=w.request_key WHERE w.account=?',(account,)).fetchall()
        outgoing=[json.loads(r[0]) for r in db.execute('SELECT signed,request FROM transfer_jobs WHERE signed IS NOT NULL ORDER BY rowid DESC') if json.loads(r[1]).get('account')==account]
    minimum=json.loads(Path('/etc/zec-tip-bot-wallet-transfers.json').read_text()).get('minimumConfirmations',10)
    if type(minimum) is not int or minimum not in (3,10):raise ValueError('Invalid confirmation policy.')
    raw=rpc.call('z_getbalances',[minimum]);matched=[r for r in raw['accounts'] if r.get('account_uuid')==account]
    if len(matched)!=1:raise ValueError('Native balance account mismatch.')
    def value(kind):
        total=0
        for pool in ('sapling','orchard','ironwood'):
            amount=(matched[0].get(pool) or {}).get(kind,{}).get('valueZat',0)
            if type(amount) is not int or amount<0:raise ValueError('Invalid native balance.')
            total+=amount
        return total
    def confirmations(signed):
        try:return rpc.call('getrawtransaction',[signed['txid'],1]).get('confirmations',0)
        except Exception:return None
    print(json.dumps({'network':'testnet','readOnly':True,'minimumSpendConfirmations':minimum,
        'confirmedSpendableIsZero':value('spendable')==0,'shieldedFundsAwaitingSpendability':value('pending')>0,
        'uneconomicNotesPresent':value('dust')>0,'welcomeGrantConfirmations':[confirmations(json.loads(r[0])) for r in grants if r[0]],
        'recentOutgoingConfirmations':[confirmations(r) for r in outgoing[:3]],
        'accountsAddressesAmountsOrTransactionIdsPrinted':False,'paymentsOrSettingsChanged':False},indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:print('Private balance check unavailable: '+(str(error) if isinstance(error,ValueError) else type(error).__name__));raise SystemExit(1)
