#!/usr/bin/env python3
"""Enable bounded Testnet enrollment and the explicitly approved welcome budget."""
from contextlib import closing
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

ARCHIVE=Path('/root/zec-public-enrollment.zip')
ARCHIVE_SHA='89c2499465e19cd4025c751d263816ebe20bebafc110ea50a7066d4c9584042e'
APP=Path('/srv/zec-tip-bot')
CONTROL=Path('/etc/zec-tip-bot-wallet-transfers.json')
NATIVE=Path('/opt/zec-tip-bot-chain')
DATA=Path('/var/lib/zec-tip-bot-wallet-broker')
UNIT='zec-tip-bot-wallet-accounts.service'
OLD={'wallet-account-broker.py':'34b7127ba946b4261b66be230a15da97bb45682471bf0ef282c1d46892a71785',
     'wallet-transfer-broker.py':'a6f56000f6ffc41a4e83edb34add8834f765f84762d26fa24c66920583c09ffc'}
WEB_FILES={'src/account.ts','src/account-ui.ts','src/private-tips.ts','src/wallet-accounts.ts','src/wallet-account-client.ts',
           'test/account-ui.test.ts','test/private-reply-context.test.ts','test/wallet-accounts.test.ts','test/wallet-account-client.test.ts',
           'deploy/wallet-account-broker.py','deploy/wallet-transfer-broker.py','deploy/wallet-enrollment.py'}
TEST_FILES={'operator/test_wallet_account_broker.py','operator/test_wallet_transfer_broker.py','operator/test_wallet_enrollment_broker.py'}
PROFILE='''import {DatabaseSync} from "node:sqlite";const db=new DatabaseSync("/app/data/bot.sqlite",{readOnly:true});try{const rows=db.prepare("SELECT w.account FROM tip_wallets w JOIN tip_profiles p ON p.user_id=w.user_id AND p.network=w.network WHERE w.network='testnet' AND w.status='ready' AND lower(p.username)='farad_0'").all();if(rows.length!==1)process.exit(1);console.log(JSON.stringify({sponsorAccount:rows[0].account}));}finally{db.close();}'''


def protected(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid!=0 or info.st_mode&0o022:
        raise ValueError('A required protected file differs from the existing deployment.')
    return info


def replace(path,raw,previous=None):
    temp=path.with_name('.enrollment-'+uuid.uuid4().hex)
    try:
        with temp.open('xb') as file:
            os.fchmod(file.fileno(),stat.S_IMODE(previous.st_mode) if previous else 0o644)
            if previous:os.fchown(file.fileno(),previous.st_uid,previous.st_gid)
            file.write(raw);file.flush();os.fsync(file.fileno())
        os.replace(temp,path)
        fd=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if temp.exists():temp.unlink()


def payload(raw):
    if hashlib.sha256(raw).hexdigest()!=ARCHIVE_SHA:raise ValueError('Enrollment release checksum did not match.')
    result={}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if len(archive.namelist())!=len(WEB_FILES|TEST_FILES) or set(archive.namelist())!=WEB_FILES|TEST_FILES:
            raise ValueError('Unexpected enrollment release entries.')
        for name in WEB_FILES|TEST_FILES:
            entry=archive.getinfo(name)
            if not 0<entry.file_size<=150000:raise ValueError('Unexpected enrollment source size.')
            result[name]=archive.read(name)
    return result


def make_control(old,accounts,sponsor,recipient):
    if (old.get('network')!='testnet' or old.get('bindingBackupVerified') is not True
            or old.get('enabled') is not True or old.get('depositsEnabled') is not True
            or not 2<=len(accounts)<=16 or len(set(accounts))!=len(accounts)
            or any(not isinstance(a,str) or not re.fullmatch(r'[a-f0-9-]{36}',a) for a in accounts)
            or hashlib.sha256('\n'.join(sorted(accounts)).encode()).hexdigest()!=old.get('accountsSha256')
            or sponsor not in accounts or not re.fullmatch(r'age1[0-9a-z]{58}',recipient)):
        raise ValueError('Existing verified Testnet account coverage was not established.')
    return {**old,'enrollment':{'enabled':True,'baselineAccounts':sorted(accounts),'recipient':recipient,
        'welcome':{'enabled':True,'sponsorAccount':sponsor,'amountZat':300000,'maxFeeZat':100000,'maximumGrants':12}}}


def activate(files,desired,base,health,visible,old_native,old_control,info):
    new_target=NATIVE/'wallet-enrollment.py'
    baseline=APP/'.update-backups'/('enrollment-'+uuid.uuid4().hex);baseline.mkdir(mode=0o700)
    for name,raw in old_native.items():(baseline/name).write_bytes(raw)
    (baseline/'transfer-control.json').write_bytes(old_control)
    stopped=False;changed=False
    try:
        # Recheck after builds, then stop and check the persisted queue again.
        base.compose('exec','-T','bot','node','--input-type=module','-e',visible.IDLE)
        base.compose('stop','-t','45','bot',quiet=False);stopped=True
        base.compose('run','--rm','--no-deps','bot','node','--input-type=module','-e',visible.IDLE)
        subprocess.run(['systemctl','stop',UNIT],check=True,timeout=45);changed=True
        if CONTROL.read_bytes()!=old_control:raise ValueError('Operator controls changed during installation.')
        for name in (*OLD,'wallet-enrollment.py'):replace(NATIVE/name,files['deploy/'+name])
        replace(CONTROL,(json.dumps(desired,indent=2)+'\n').encode(),info)
        subprocess.run(['systemctl','start',UNIT],check=True,timeout=45)
        for attempt in range(30):
            try:
                now=health.read('/health')
                if now.get('accountCreationReady') is True and now.get('transfersEnabled') is True and now.get('accountAdmissionRequired') is True:break
            except Exception:pass
            if attempt==29:raise ValueError('New enrollment did not reach Testnet readiness.')
            time.sleep(2)
        base.compose('start','bot',quiet=False);stopped=False;base.wait_web()
    except BaseException:
        if changed:
            if not stopped:base.compose('stop','-t','45','bot',quiet=False);stopped=True
            subprocess.run(['systemctl','stop',UNIT],check=True,timeout=45)
            rollback=False
            try:
                with closing(sqlite3.connect('file:'+str(DATA/'accounts.sqlite')+'?mode=ro',uri=True)) as db:
                    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    rollback=not any(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('enrollment_bindings','welcome_grants') if table in tables)
            except Exception:pass
            if rollback:
                replace(CONTROL,old_control,info)
                for name,raw in old_native.items():replace(NATIVE/name,raw)
                if new_target.is_file():new_target.unlink()
            else:
                # Never undo coverage after a real signup has reached the new
                # backend. Preserve every account/job and hold further admission.
                held=json.loads(json.dumps(desired));held['enrollment']['enabled']=False
                held['enrollment']['welcome']['enabled']=False
                replace(CONTROL,(json.dumps(held,indent=2)+'\n').encode(),info)
            subprocess.run(['systemctl','start',UNIT],check=True,timeout=45)
        raise
    finally:
        if stopped:base.compose('start','bot',quiet=False)


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the VPS root terminal.')
    os.umask(0o077)
    path=Path('/root/update-visible-settings.py');protected(path)
    spec=importlib.util.spec_from_file_location('enrollment_visible',path);visible=importlib.util.module_from_spec(spec);spec.loader.exec_module(visible)
    base=visible.load(Path('/root/update-dashboard.py'),'enrollment_base',visible.BASE_SHA)
    health=visible.load(Path('/root/enable-testnet-deposits.py'),'enrollment_health')
    info=protected(CONTROL);old_control=CONTROL.read_bytes();control=json.loads(old_control)
    if control.get('enrollment') is not None:
        print(json.dumps({'publicEnrollmentAlreadyConfigured':True,'network':'testnet',
                          'settingsOrGrantsChanged':False,'action':'Run check-public-enrollment.py for current readiness.'},indent=2));return
    files=payload(ARCHIVE.read_bytes())
    old_native={}
    for name,expected in OLD.items():
        target=NATIVE/name;protected(target);raw=target.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('The installed native adapter differs from the verified baseline.')
        old_native[name]=raw
    new_target=NATIVE/'wallet-enrollment.py'
    if new_target.exists() or new_target.is_symlink():raise ValueError('An enrollment module already exists; it was preserved.')
    with tempfile.TemporaryDirectory(prefix='zec-enrollment-tests-') as temp:
        folder=Path(temp)
        for name,raw in files.items():
            if name.startswith(('deploy/','operator/')):
                target=folder/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        subprocess.run(['/usr/bin/python3','-m','unittest','discover','-s','operator','-p','test_wallet_*broker.py'],cwd=folder,check=True,timeout=90)
    base.compose('exec','-T','bot','node','--input-type=module','-e',visible.IDLE)
    before=health.read('/health')
    if before.get('transfersEnabled') is not True or before.get('backupConfirmed') is not True:raise ValueError('Wait for current Testnet wallet readiness.')
    with closing(sqlite3.connect('file:'+str(DATA/'accounts.sqlite')+'?mode=ro',uri=True)) as db:
        accounts=[r[0] for r in db.execute("SELECT account FROM account_intents WHERE phase='created' ORDER BY account")]
        if db.execute("SELECT COUNT(*) FROM transfer_jobs WHERE phase NOT IN ('submitted','rejected')").fetchone()[0]:raise ValueError('Resolve outstanding native payment work first.')
    sponsor=json.loads(base.compose('exec','-T','bot','node','--input-type=module','-e',PROFILE).stdout)['sponsorAccount']
    backup_state=Path('/root/zec-tip-bot-testnet-recovery/recovery-state.json');protected(backup_state)
    recipient=json.loads(backup_state.read_text())['backupRecipient']
    desired=make_control(control,accounts,sponsor,recipient)
    balance=health.read('/balance/'+sponsor)
    if (balance.get('network')!='testnet' or balance.get('account')!=sponsor
            or type(balance.get('confirmedSpendableZat')) is not int or balance['confirmedSpendableZat']<4_800_000):
        raise ValueError('The approved sponsor does not have the full 0.048 TAZ budget available.')
    probe=subprocess.run(['runuser','-u','zectip-chain','--','/usr/bin/age','--encrypt','--recipient',recipient],
                         input=b'public enrollment encryption capability check',capture_output=True,timeout=20)
    if probe.returncode or not probe.stdout.startswith(b'age-encryption.org/v1'):raise ValueError('The service could not encrypt to the existing recovery recipient.')
    # Build/test the website while enrollment remains closed on the old broker.
    original_compose=base.compose
    base.payload=lambda _: {n:files[n] for n in WEB_FILES}
    visible.ARCHIVE=ARCHIVE;visible.ARCHIVE_SHA=ARCHIVE_SHA;visible.FILES=WEB_FILES
    report=visible.install(base,health)
    base.compose=original_compose
    activate(files,desired,base,health,visible,old_native,old_control,info)
    print(json.dumps({'network':'testnet','publicEnrollmentEnabled':True,'existingWalletsPreserved':len(accounts),
        'encryptedSnapshotRequiredBeforeAdmission':True,'newSnapshotOffServerVerification':False,
        'welcomeAmountTaz':'0.003','maximumWelcomeGrants':12,'maximumWelcomeCostIncludingFeesTaz':'0.048',
        'welcomeGrantsRequestedByInstaller':0,'accountsCreatedByInstaller':0,
        'userAutomaticSpendingEnabledByInstaller':False,'mainnetEnabled':False,
        'workerConfigurationPreserved':report['workerConfigurationPreserved'],'privateEnvironmentChanged':False},indent=2))
    print('Test one fresh X signup. Keep new encrypted enrollment snapshots copied off the VPS.')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Enrollment update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
