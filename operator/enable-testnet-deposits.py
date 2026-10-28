#!/usr/bin/env python3
"""Enable private Testnet deposit display only after off-server linked recovery succeeds."""
from contextlib import closing
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import sys
import uuid

ARCHIVE_SHA='4308f3d7dc3bed9bc2f095454e13381e3cf2634bf43c3f64f525765767cf21e7'
PROOF=Path('/root/linked-wallets-verification.json')
RECEIPT=Path('/root/zec-tip-bot-testnet-recovery/linked-wallets-state.json')
CONTROL=Path('/etc/zec-tip-bot-wallet-transfers.json')
SOCKET='/run/zec-tip-bot-wallet-broker/wallet.sock'
BROKER_DB=Path('/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite')


def protected_json(path,private=False):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or info.st_size>32768:
        raise ValueError('Expected the protected verification metadata.')
    if private and info.st_mode&0o077:raise ValueError('Expected a private recovery receipt.')
    return json.loads(path.read_text())


def verify_report(proof,record):
    flags=('linkedBackupDecryptionVerified','walletDatabaseIntegrityVerified','operatorSeedRecoveryVerified',
           'nativeBackupConfirmed','accountMappingsVerified')
    if (not isinstance(proof,dict) or not isinstance(record,dict) or proof.get('network')!='testnet'
            or record.get('network')!='testnet' or proof.get('encryptedBackupSha256')!=ARCHIVE_SHA
            or record.get('archiveSha256')!=ARCHIVE_SHA or any(proof.get(key) is not True for key in flags)
            or proof.get('changesServer') is not False or type(proof.get('walletAccounts')) is not int
            or proof['walletAccounts']!=record.get('walletAccounts') or not 2<=proof['walletAccounts']<=16):
        raise ValueError('The local verification report did not match the prepared linked-wallet backup.')
    for field in ('accountsSha256','bindingsSha256'):
        if not isinstance(proof.get(field),str) or not re.fullmatch(r'[a-f0-9]{64}',proof[field]) or proof[field]!=record.get(field):
            raise ValueError('Local account mapping verification differs from the server backup.')
    return {'network':'testnet','enabled':False,'depositsEnabled':True,'bindingBackupVerified':True,
            'accountsSha256':proof['accountsSha256'],'linkedArchiveSha256':ARCHIVE_SHA}


class LocalConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(30);self.sock.connect(SOCKET)


def read(path):
    connection=LocalConnection('localhost',timeout=30)
    try:
        connection.request('GET',path);response=connection.getresponse();raw=response.read(65537)
        if response.status!=200 or len(raw)>65536:raise ValueError('The private wallet service did not pass its readiness check.')
        return json.loads(raw)
    finally:connection.close()


def atomic_json(path,value,group=None):
    temporary=path.with_name('.'+path.name+'-'+uuid.uuid4().hex)
    try:
        with temporary.open('x',encoding='utf-8') as file:
            os.chmod(temporary,0o640 if group is not None else 0o600)
            if group is not None:os.chown(temporary,0,group)
            json.dump(value,file,indent=2);file.flush();os.fsync(file.fileno())
        os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()


def current_accounts():
    with closing(sqlite3.connect(BROKER_DB.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        rows=db.execute("SELECT account FROM account_intents WHERE phase='created' ORDER BY account").fetchall()
    return len(rows),hashlib.sha256('\n'.join(row[0] for row in rows).encode()).hexdigest()


def load_base():
    path=Path('/root/update-wallet-balances.py');info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected the installed protected balance helper.')
    spec=importlib.util.spec_from_file_location('deposit_base',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    os.umask(0o077)
    proof=protected_json(PROOF);record=protected_json(RECEIPT,True);desired=verify_report(proof,record)
    old=load_base();pool,recovery,state=old.private_context()
    old.compose('exec','-T','bot','node','-e',old.PREFLIGHT_JS)
    archive=Path(record['archive'])
    if archive!=Path('/root/zec-tip-bot-testnet-recovery/linked-wallets-before-funding.tar.age') or archive.is_symlink() or recovery.file_digest(archive)!=ARCHIVE_SHA:
        raise ValueError('The encrypted linked backup differs from the verified archive.')
    count,digest=current_accounts()
    if count!=proof['walletAccounts'] or digest!=proof['accountsSha256']:raise ValueError('The wallet account set changed after backup. Deposits remain held.')
    health=read('/health')
    if (not isinstance(health,dict) or health.get('network')!='testnet' or health.get('synced') is not True
            or health.get('backupConfirmed') is not True or health.get('currentConsensusVerified') is not True
            or health.get('transfersEnabled') is not False):raise ValueError('The synchronized, spending-disabled Testnet wallet is required.')
    previous=protected_json(CONTROL) if CONTROL.exists() else None
    if CONTROL.is_symlink() or (previous is not None and previous!=desired):raise ValueError('An existing different operator control file was preserved.')
    import grp
    group=grp.getgrnam('zectip-wallet-client').gr_gid
    if not 100<=group<=65535:raise ValueError('Unexpected private wallet client group.')
    try:
        atomic_json(CONTROL,desired,group)
        final=read('/health')
        if final.get('network')!='testnet' or not final.get('synced') or final.get('depositsEnabled') is not True or final.get('transfersEnabled') is not False:
            raise ValueError('Private deposit readiness did not pass. Operator controls were restored.')
        record.update(offServerVerified=True,verificationReportSha256=hashlib.sha256(PROOF.read_bytes()).hexdigest())
        atomic_json(RECEIPT,record)
    except BaseException:
        if previous is None:
            if CONTROL.exists() and json.loads(CONTROL.read_text())==desired:CONTROL.unlink()
        else:atomic_json(CONTROL,previous,group)
        raise
    print(json.dumps({'network':'testnet','walletAccounts':count,'offServerSeedAndBindingsVerified':True,
                      'depositsEnabled':True,'transfersEnabled':False,'postingChanged':False,
                      'privateEnvironmentChanged':False,'addressesPrinted':False},indent=2))
    print('Your private account page can now show its Testnet deposit address. Automatic tips and withdrawals are still disabled.')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Testnet deposit setup stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
