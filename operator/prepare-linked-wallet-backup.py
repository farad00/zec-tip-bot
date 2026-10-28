#!/usr/bin/env python3
"""Encrypt the current wallet AND X-account bindings before accepting Testnet deposits."""
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import uuid

APP=Path('/srv/zec-tip-bot')
ROOT=Path('/root/zec-tip-bot-testnet-recovery')
ARCHIVE=ROOT/'linked-wallets-before-funding.tar.age'
RECEIPT=ROOT/'linked-wallets-state.json'
BROKER=Path('/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite')
ACCOUNT_UNIT='zec-tip-bot-wallet-accounts.service'
WALLET_UNIT='zec-tip-bot-wallet-testnet.service'
NAMES={'wallet.db','app.db','broker.db','zallet.toml','encryption-identity.txt','app.env','broker-config.json','compose.json'}


def load(path,name):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected a protected existing helper.')
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def compose(*args,**kwargs):
    return subprocess.run(['docker','compose','--env-file','.env','-f','deploy/compose.yaml',*args],cwd=APP,check=True,**kwargs)


def snapshot(source,target):
    if source.is_symlink() or not source.is_file():raise ValueError('Expected an existing database file.')
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)) as old,closing(sqlite3.connect(target)) as new:
        old.backup(new)
        if new.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('A database backup did not pass its integrity check.')
    target.chmod(0o600)


def binding_metadata(app_path,broker_path):
    with closing(sqlite3.connect(app_path)) as app,closing(sqlite3.connect(broker_path)) as broker:
        rows=app.execute("SELECT user_id,account,address,status FROM tip_wallets WHERE network='testnet' ORDER BY account").fetchall()
        native=broker.execute("SELECT account FROM account_intents WHERE phase='created' ORDER BY account").fetchall()
        pending=app.execute("SELECT COUNT(*) FROM tip_jobs WHERE status NOT IN ('failed','confirmed')").fetchone()[0]
    if (not 2<=len(rows)<=16 or [r[1] for r in rows]!=[r[0] for r in native]
            or len({r[0] for r in rows})!=len(rows) or any(r[3]!='ready' for r in rows)):
        raise ValueError('Existing X-account and wallet bindings did not match.')
    if pending:raise ValueError('Resolve pending payments before preparing the pre-funding backup.')
    return {'walletAccounts':len(rows),'accountsSha256':hashlib.sha256('\n'.join(r[1] for r in rows).encode()).hexdigest(),
            'bindingsSha256':hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()}


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    os.umask(0o077)
    install=load(Path('/root/update-wallet-balances.py'),'linked_backup_install')
    pool,recovery,state=install.private_context()
    recipient=state.get('backupRecipient');recovery.validate_recipient(recipient)
    compose('exec','-T','bot','node','-e',install.PREFLIGHT_JS)
    control=Path('/etc/zec-tip-bot-wallet-transfers.json')
    if control.exists():
        value=json.loads(control.read_text())
        if value.get('enabled') is not False or value.get('depositsEnabled') is not False:raise ValueError('Disable deposits and transfers before this pre-funding backup.')
    if ARCHIVE.exists():
        recovery.protected_file(RECEIPT,0);record=json.loads(RECEIPT.read_text())
        if record.get('archiveSha256')!=recovery.file_digest(ARCHIVE):raise ValueError('The existing linked backup differs from its receipt.')
        print(json.dumps({'linkedWalletBackupPrepared':True,'encryptedBackup':str(ARCHIVE),'encryptedBackupSha256':record['archiveSha256'],
                          'walletAccounts':record['walletAccounts'],'offServerVerified':record.get('offServerVerified',False),
                          'depositsEnabled':False,'transfersEnabled':False},indent=2));return
    if RECEIPT.exists():raise ValueError('A partial backup record exists. Existing files were preserved.')
    container=compose('ps','-q','bot',capture_output=True,text=True).stdout.strip()
    if not __import__('re').fullmatch(r'[a-f0-9]{12,64}',container):raise ValueError('Expected the existing website container.')
    stage=ROOT/('.linked-stage-'+uuid.uuid4().hex);stage.mkdir(mode=0o700)
    web_stopped=False;broker_stopped=False;wallet_stopped=False
    try:
        print('Briefly pausing the website and wallet to capture matching recovery snapshots...',flush=True)
        web_stopped=True;compose('stop','-t','45','bot')
        broker_stopped=True;pool.run('systemctl','stop',ACCOUNT_UNIT,timeout=45)
        wallet_stopped=True;pool.run('systemctl','stop',WALLET_UNIT,timeout=45)
        raw=stage/'app-raw.db'
        subprocess.run(['docker','cp',container+':/app/data/bot.sqlite',str(raw)],check=True,capture_output=True)
        for suffix in ('-wal','-shm'):
            # A stopped container may have no WAL; the mandatory base copy above
            # establishes container access. Existing journals are preserved if present.
            result=subprocess.run(['docker','cp',container+':/app/data/bot.sqlite'+suffix,str(raw)+suffix],capture_output=True)
            if result.returncode and b'Could not find' not in result.stderr and b'No such' not in result.stderr:
                raise ValueError('Could not copy the application journal consistently.')
        snapshot(raw,stage/'app.db');snapshot(BROKER,stage/'broker.db');snapshot(recovery.DATA/'wallet.db',stage/'wallet.db')
        bindings=binding_metadata(stage/'app.db',stage/'broker.db')
        metadata=recovery.metadata(stage/'wallet.db')
        if not metadata['confirmed'] or metadata['fingerprint']!=state['fingerprint'] or metadata['accounts']!=bindings['walletAccounts']:
            raise ValueError('Native wallet recovery metadata differs from the account bindings.')
        sources={'zallet.toml':recovery.DATA/'zallet.toml','encryption-identity.txt':recovery.DATA/'encryption-identity.txt',
                 'app.env':APP/'.env','broker-config.json':Path('/etc/zec-tip-bot-wallet-broker.json'),
                 'compose.json':APP/'deploy/compose.yaml'}
        for name,source in sources.items():
            if source.is_symlink() or not source.is_file():raise ValueError('Expected a fixed recovery configuration file.')
            shutil.copyfile(source,stage/name);(stage/name).chmod(0o600)
        files={name:stage/name for name in NAMES}
        manifest={'schema':2,'network':'testnet','sourceRevision':recovery.REVISION,'seedFingerprintHex':state['fingerprint'],
                  **bindings,'fileSha256':{name:recovery.file_digest(path) for name,path in files.items()},
                  'restoreRequiresDisabledWorkerAndFreshCommandWindow':True}
        digest=recovery.encrypt_archive(files,ARCHIVE,manifest,recipient)
        record={'network':'testnet','archive':str(ARCHIVE),'archiveSha256':digest,**bindings,'offServerVerified':False}
        with RECEIPT.open('x',encoding='utf-8') as file:json.dump(record,file,indent=2);file.flush();os.fsync(file.fileno())
        RECEIPT.chmod(0o600)
        print(json.dumps({'linkedWalletBackupPrepared':True,'encryptedBackup':str(ARCHIVE),'encryptedBackupSha256':digest,
                          'walletAccounts':bindings['walletAccounts'],'offServerVerified':False,
                          'depositsEnabled':False,'transfersEnabled':False,'createsKeysOrAccounts':False},indent=2),flush=True)
    finally:
        # Restore independent services even if one restoration encounters a fault.
        failures=[]
        for needed,action in ((wallet_stopped,lambda:pool.run('systemctl','start',WALLET_UNIT,timeout=45)),
                              (broker_stopped,lambda:pool.run('systemctl','start',ACCOUNT_UNIT,timeout=45)),
                              (web_stopped,lambda:compose('start','bot'))):
            if needed:
                try:action()
                except Exception:failures.append(True)
        if stage.resolve().parent!=ROOT.resolve() or not stage.name.startswith('.linked-stage-'):raise ValueError('Unexpected backup staging path.')
        shutil.rmtree(stage)
        if failures:raise ValueError('Backup completed or was held, but a paused service needs restoration. No funding was enabled.')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Linked-wallet backup stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
