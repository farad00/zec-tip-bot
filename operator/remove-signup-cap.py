#!/usr/bin/env python3
"""Remove the wallet-count cap while preserving the approved welcome budget."""
from contextlib import closing
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

APP=Path('/srv/zec-tip-bot')
NATIVE=Path('/opt/zec-tip-bot-chain')
CONTROL=Path('/etc/zec-tip-bot-wallet-transfers.json')
DATABASE=Path('/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite')
UNIT='zec-tip-bot-wallet-accounts.service'
ARCHIVE=Path('/root/zec-open-signups.zip')
ARCHIVE_SHA='1f452ca8490826441643d6b04dc155fc30ab3f5d46b9025e25df689fe09c0000'
OLD={'wallet-account-broker.py':'9660019e538be78a04d20d388261873df187fac232e5e58740cdecb26870ad47',
     'wallet-transfer-broker.py':'32485ae0b9cb77db609af85901814ce26d00b8a5375f287a0bd047492490617f'}
ENROLLMENT_SHA='e3698c23d6965cc4fc830ec43d978ce51b64dbda2741f1a75faab05f154a628a'
WEB_FILES={'src/wallet-account-client.ts','test/wallet-account-client.test.ts',
           'deploy/wallet-account-broker.py','deploy/wallet-transfer-broker.py'}
TEST_FILES={'operator/test_wallet_account_broker.py','operator/test_wallet_transfer_broker.py','operator/test_wallet_enrollment_broker.py'}
FILES=WEB_FILES|TEST_FILES|{'deploy/wallet-enrollment.py'}


def policy(raw):
    value=json.loads(raw);enrollment=value.get('enrollment',{});grant=enrollment.get('welcome',{})
    if (value.get('network')!='testnet' or value.get('bindingBackupVerified') is not True
            or value.get('enabled') is not True or value.get('depositsEnabled') is not True
            or enrollment.get('enabled') is not True or grant.get('maximumGrants')!=12
            or type(grant.get('maximumGrants')) is not int or grant.get('amountZat')!=300_000
            or grant.get('maxFeeZat')!=100_000 or value.get('minimumConfirmations',10) not in (3,10)):
        raise ValueError('Expected the existing Testnet enrollment and unchanged 12-grant authorization.')
    return value


def payload(raw):
    if hashlib.sha256(raw).hexdigest()!=ARCHIVE_SHA:raise ValueError('Signup update checksum did not match.')
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        if set(zipped.namelist())!=FILES or len(zipped.namelist())!=len(FILES):raise ValueError('Unexpected signup update source list.')
        if any(not 0<zipped.getinfo(name).file_size<=150_000 for name in FILES):raise ValueError('Unexpected source size.')
        return {name:zipped.read(name) for name in FILES}


def records():
    # Compare records internally; no identities, terms, addresses or transactions
    # are printed. The updater never changes or deletes a database record.
    with closing(sqlite3.connect('file:'+str(DATABASE)+'?mode=ro',uri=True)) as db:
        tables=('account_intents','enrollment_bindings','welcome_grants','transfer_jobs')
        digest=hashlib.sha256()
        for table in tables:
            digest.update(table.encode());digest.update(repr(db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()).encode())
        count=db.execute('SELECT COUNT(*) FROM welcome_grants').fetchone()[0]
    if count>12:raise ValueError('Existing welcome records exceed the approved grant count.')
    return digest.hexdigest(),count


def activate(files,old,control_raw,base,health,atomic,idle):
    backup=APP/'.update-backups'/('signup-count-'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
    for name,raw in old.items():(backup/name).write_bytes(raw)
    phase=backup/'state.json'
    def state(value):phase.write_text(json.dumps({'phase':value,'walletCountCapRemoved':True}),encoding='utf8')
    website_stopped=False;broker_stopped=False;changed=False
    try:
        base.compose('exec','-T','bot','node','--input-type=module','-e',idle.IDLE);idle.native_idle()
        base.compose('stop','-t','45','bot',quiet=False);website_stopped=True
        base.compose('run','--rm','--no-deps','bot','node','--input-type=module','-e',idle.IDLE);idle.native_idle()
        broker_stopped=True;subprocess.run(['systemctl','stop',UNIT],check=True,timeout=45)
        idle.native_idle();before,count=records()
        if CONTROL.read_bytes()!=control_raw:raise ValueError('Operator controls changed during the update.')
        state('installing');changed=True
        for name in old:atomic.replace(NATIVE/name,files['deploy/'+name],atomic.protected(NATIVE/name))
        subprocess.run(['systemctl','start',UNIT],check=True,timeout=45);broker_stopped=False
        for attempt in range(30):
            try:
                result=health.read('/health')
                if (result.get('network')=='testnet' and result.get('accountCountCapEnabled') is False
                        and result.get('transfersEnabled') is True and result.get('accountCreationReady') is True
                        and result.get('accountAdmissionRequired') is True):break
            except Exception:pass
            if attempt==29:raise ValueError('The account service did not reach uncapped enrollment readiness.')
            time.sleep(2)
        if CONTROL.read_bytes()!=control_raw or records()!=(before,count):raise ValueError('Operator controls or existing wallet/payment records changed; inspect the preserved state.')
        state('backend_verified')
        return count
    except BaseException:
        if changed:
            # The website is still stopped, so new commands/admissions cannot
            # appear through it while the previous native source is restored.
            broker_stopped=True;subprocess.run(['systemctl','stop',UNIT],check=True,timeout=45)
            for name,raw in old.items():atomic.replace(NATIVE/name,raw,atomic.protected(NATIVE/name))
            subprocess.run(['systemctl','start',UNIT],check=True,timeout=45);broker_stopped=False
            state('rolled_back')
        raise
    finally:
        if broker_stopped:subprocess.run(['systemctl','start',UNIT],check=True,timeout=45)
        if website_stopped:base.compose('start','bot',quiet=False)


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    os.umask(0o077)
    path=Path('/root/update-visible-settings.py')
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid!=0 or info.st_mode&0o022
            or hashlib.sha256(path.read_bytes()).hexdigest()!='2914c6a38df74497883ab7adb3300851999523cc57badc7e98df1cb1c47ba4bf'):
        raise ValueError('Expected the verified runtime-preserving updater.')
    spec=importlib.util.spec_from_file_location('signup_count_visible',path)
    visible=importlib.util.module_from_spec(spec);spec.loader.exec_module(visible)
    base=visible.load(Path('/root/update-dashboard.py'),'signup_count_base',visible.BASE_SHA)
    health=visible.load(Path('/root/enable-testnet-deposits.py'),'signup_count_health')
    atomic=visible.load(Path('/root/install-public-enrollment.py'),'signup_count_atomic')
    idle=visible.load(Path('/root/update-confirming-balance.py'),'signup_count_idle')
    files=payload(ARCHIVE.read_bytes());atomic.protected(CONTROL);control_raw=CONTROL.read_bytes();policy(control_raw)
    enrollment=NATIVE/'wallet-enrollment.py';atomic.protected(enrollment)
    if hashlib.sha256(enrollment.read_bytes()).hexdigest()!=ENROLLMENT_SHA:raise ValueError('The installed recovery/grant adapter differs from the verified baseline.')
    old={}
    for name,expected in OLD.items():
        path=NATIVE/name;atomic.protected(path);raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest() not in {expected,hashlib.sha256(files['deploy/'+name]).hexdigest()}:
            raise ValueError('The installed account adapter differs from the verified deployment.')
        old[name]=raw
    idle.native_idle()
    # Run all broker tests with synthetic state and the exact candidate sources.
    with tempfile.TemporaryDirectory(prefix='zec-signup-count-tests-') as temp:
        folder=Path(temp)
        for name,raw in files.items():
            if name.startswith(('deploy/','operator/')):
                target=folder/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        subprocess.run(['/usr/bin/python3','-m','unittest','discover','-s','operator','-p','test_wallet_*broker.py'],cwd=folder,check=True,timeout=90)
    original_compose=base.compose
    base.payload=lambda raw:{name:value for name,value in payload(raw).items() if name in WEB_FILES}
    visible.ARCHIVE=ARCHIVE;visible.ARCHIVE_SHA=ARCHIVE_SHA;visible.FILES=WEB_FILES;visible.IDLE=idle.IDLE
    visible.install(base,health);base.compose=original_compose
    count=activate(files,old,control_raw,base,health,atomic,idle);base.wait_web()
    print(json.dumps({'network':'testnet','walletCountCapRemoved':True,'publicEnrollmentReady':True,
        'maximumWelcomeGrants':12,'welcomeAmountTaz':'0.003','maximumWelcomeCostIncludingFeesTaz':'0.048',
        'welcomeGrantsAlreadyRecorded':count,'welcomeBudgetAndHistoryPreserved':True,
        'encryptedAdmissionStillRequired':True,'existingWalletAndPaymentRecordsPreserved':True,
        'workerConfigurationPreserved':True,'walletOrNodeRestarted':False,
        'accountsCreatedByInstaller':0,'paymentsRequestedByInstaller':0,'privateEnvironmentChanged':False},indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Signup count update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
