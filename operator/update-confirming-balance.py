#!/usr/bin/env python3
"""Show native pending funds and configure the Testnet-only three-confirmation pilot."""
from contextlib import closing
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid
import zipfile

ARCHIVE=Path('/root/zec-confirming-balance.zip')
ARCHIVE_SHA='2dca327eda49eef86d3071205aa03995f3f820c07f6484fbd69a48628037449c'
FILES={'src/account.ts','src/account-ui.ts','src/wallet-accounts.ts','src/wallet-account-client.ts',
       'test/account-ui.test.ts','test/wallet-account-client.test.ts',
       'deploy/wallet-transfer-broker.py','deploy/wallet-enrollment.py'}
NATIVE=Path('/opt/zec-tip-bot-chain')
CONTROL=Path('/etc/zec-tip-bot-wallet-transfers.json')
DATABASE=Path('/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite')
UNIT='zec-tip-bot-wallet-accounts.service'
OLD={'wallet-transfer-broker.py': '2106b1281b56609b656082a83eb606b6ffdb4679ad003a8c01cd1b076fec3a6a', 'wallet-enrollment.py': '8c08cd545fdd265fef9c130df6c829ebff9dbb09a066918178535c3fc38bfd56'}
IDLE='''import {DatabaseSync} from "node:sqlite";const db=new DatabaseSync("/app/data/bot.sqlite",{readOnly:true});try{if(db.prepare("SELECT COUNT(*) AS n FROM tip_jobs WHERE status IN ('queued','preparing','prepared','signing','signed','broadcasting','submitted')").get().n||db.prepare("SELECT COUNT(*) AS n FROM wallet_account_leases WHERE expires>?").get(Date.now()).n)process.exit(1);}finally{db.close();}'''


def configured(control):
    if (control.get('network')!='testnet' or control.get('enabled') is not True
            or control.get('bindingBackupVerified') is not True or type(control.get('minimumConfirmations',10)) is not int
            or control.get('minimumConfirmations',10) not in (3,10)):
        raise ValueError('Only the existing verified Testnet pilot can use this policy.')
    return {**control,'minimumConfirmations':3}


def native_idle():
    with closing(sqlite3.connect('file:'+str(DATABASE)+'?mode=ro',uri=True)) as db:
        if db.execute("SELECT COUNT(*) FROM transfer_jobs WHERE phase NOT IN ('submitted','rejected')").fetchone()[0]:raise ValueError('Resolve active or uncertain native payment work first.')
        if db.execute("SELECT COUNT(*) FROM welcome_grants WHERE phase NOT IN ('submitted','failed')").fetchone()[0]:raise ValueError('Wait for welcome grants or reconcile their hold first.')
        if db.execute("SELECT COUNT(*) FROM enrollment_bindings WHERE phase='snapshotting'").fetchone()[0]:raise ValueError('Wait for wallet setup to complete.')


def activate(files,old,old_control,info,desired,base,health,atomic):
    backup=Path('/srv/zec-tip-bot/.update-backups')/('confirming-balance-'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
    for name,raw in old.items():(backup/name).write_bytes(raw)
    (backup/'transfer-control.json').write_bytes(old_control)
    stopped=False;changed=False
    try:
        base.compose('exec','-T','bot','node','--input-type=module','-e',IDLE);native_idle()
        base.compose('stop','-t','45','bot',quiet=False);stopped=True
        base.compose('run','--rm','--no-deps','bot','node','--input-type=module','-e',IDLE);native_idle()
        subprocess.run(['systemctl','stop',UNIT],check=True,timeout=45);changed=True
        if CONTROL.read_bytes()!=old_control:raise ValueError('Operator controls changed during the update.')
        for name in old:atomic.replace(NATIVE/name,files['deploy/'+name])
        atomic.replace(CONTROL,(json.dumps(desired,indent=2)+'\n').encode(),info)
        subprocess.run(['systemctl','start',UNIT],check=True,timeout=45)
        for attempt in range(30):
            try:
                result=health.read('/health')
                if result.get('minimumConfirmations')==3 and result.get('transfersEnabled') is True:break
            except Exception:pass
            if attempt==29:raise ValueError('The updated Testnet balance policy did not become ready.')
            time.sleep(2)
        with closing(sqlite3.connect('file:'+str(DATABASE)+'?mode=ro',uri=True)) as db:
            accounts=set(desired.get('enrollment',{}).get('baselineAccounts',[]))
            accounts.update(r[0] for r in db.execute("SELECT account FROM enrollment_bindings WHERE phase='active'"))
        confirmed_grants=0
        for account in sorted(accounts):
            value=health.read('/balance/'+account)
            if (value.get('network')!='testnet' or value.get('account')!=account or value.get('minimumConfirmations')!=3
                    or any(type(value.get(key)) is not int or value[key]<0 for key in ('confirmedSpendableZat','pendingZat','uneconomicZat'))):
                raise ValueError('A private account balance probe did not pass.')
            grant=health.read('/welcome/'+account)
            if grant.get('state')=='confirmed':confirmed_grants+=1
        base.compose('start','bot',quiet=False);stopped=False;base.wait_web()
        return len(accounts),confirmed_grants
    except BaseException:
        if changed:
            if not stopped:base.compose('stop','-t','45','bot',quiet=False);stopped=True
            subprocess.run(['systemctl','stop',UNIT],check=True,timeout=45)
            # Preserve every database and payment record. Only the source/config
            # are restored; a signed transaction is never recreated by an updater.
            atomic.replace(CONTROL,old_control,info)
            for name,raw in old.items():atomic.replace(NATIVE/name,raw)
            subprocess.run(['systemctl','start',UNIT],check=True,timeout=45)
        raise
    finally:
        if stopped:base.compose('start','bot',quiet=False)


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the VPS root terminal.')
    os.umask(0o077)
    path=Path('/root/update-visible-settings.py')
    spec=importlib.util.spec_from_file_location('confirming_visible',path);visible=importlib.util.module_from_spec(spec);spec.loader.exec_module(visible)
    base=visible.load(Path('/root/update-dashboard.py'),'confirming_base',visible.BASE_SHA)
    health=visible.load(Path('/root/enable-testnet-deposits.py'),'confirming_health')
    atomic=visible.load(Path('/root/install-public-enrollment.py'),'confirming_atomic')
    raw=ARCHIVE.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ARCHIVE_SHA:raise ValueError('Balance update checksum did not match.')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if len(archive.namelist())!=len(FILES) or set(archive.namelist())!=FILES:raise ValueError('Unexpected balance update source list.')
        files={name:archive.read(name) for name in FILES}
    info=atomic.protected(CONTROL);old_control=CONTROL.read_bytes();desired=configured(json.loads(old_control))
    old={}
    for name,expected in OLD.items():
        target=NATIVE/name;atomic.protected(target);content=target.read_bytes()
        if hashlib.sha256(content).hexdigest() not in {expected,hashlib.sha256(files['deploy/'+name]).hexdigest()}:raise ValueError('Native source differs from the verified enrollment adapter.')
        old[name]=content
    native_idle();base.compose('exec','-T','bot','node','--input-type=module','-e',IDLE)
    compose=base.compose;visible.IDLE=IDLE
    visible.ARCHIVE=ARCHIVE;visible.ARCHIVE_SHA=ARCHIVE_SHA;visible.FILES=FILES
    visible.install(base,health);base.compose=compose
    count,confirmed=activate(files,old,old_control,info,desired,base,health,atomic)
    print(json.dumps({'network':'testnet','confirmingBalanceDisplayInstalled':True,'welcomeStatusUsesActualConfirmations':True,
        'minimumSpendConfirmations':3,'privateAccountsChecked':count,'confirmedWelcomeStatuses':confirmed,
        'spendingCapsFeeLimitsAndGrantBudgetChanged':False,'newPaymentsRequestedByUpdater':False,
        'nativeWalletOrNodeRestarted':False,'workerConfigurationPreserved':True,'privateEnvironmentChanged':False},indent=2))
    print('Refresh the wallet. Available and confirming funds are separate; Testnet spending now requires 3 confirmations.')


if __name__=='__main__':
    try:main()
    except Exception as error:print('Balance update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__));raise SystemExit(1)
