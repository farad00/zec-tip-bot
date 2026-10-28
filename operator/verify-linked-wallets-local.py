#!/usr/bin/env python3
"""Human-run verification of the linked-account backup. Plaintext stays in memory."""
from contextlib import closing
import argparse
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tarfile
import tomllib
import uuid

spec=importlib.util.spec_from_file_location('original_local_recovery',Path(__file__).with_name('verify-testnet-recovery-local.py'))
recovery=importlib.util.module_from_spec(spec);spec.loader.exec_module(recovery)
RecoveryError=recovery.RecoveryError
LIMITS={'wallet.db':512*1024*1024,'app.db':128*1024*1024,'broker.db':32*1024*1024,
        'zallet.toml':65536,'encryption-identity.txt':16384,'app.env':65536,
        'broker-config.json':16384,'compose.json':65536,'manifest.json':32768}
NAME='linked-wallets-before-funding.tar.age'


def database(raw):
    if not raw.startswith(b'SQLite format 3\x00') or len(raw)<100:raise RecoveryError('Invalid linked database snapshot.')
    copied=bytearray(raw)
    if copied[18] not in (1,2) or copied[19] not in (1,2):raise RecoveryError('Unexpected database journal format.')
    copied[18]=copied[19]=1
    db=sqlite3.connect(':memory:')
    try:
        db.deserialize(bytes(copied));db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise RecoveryError('Linked database integrity verification failed.')
        return db
    except Exception:db.close();raise


def read(raw):
    if len(raw)>sum(LIMITS.values())+65536:raise RecoveryError('Linked archive exceeds its size limit.')
    files={}
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
        for entry in archive:
            if entry.name not in LIMITS or entry.name in files or not entry.isfile() or not 0<entry.size<=LIMITS[entry.name]:
                raise RecoveryError('Linked archive contains an unexpected entry.')
            with archive.extractfile(entry) as file:files[entry.name]=file.read(entry.size+1)
            if len(files[entry.name])!=entry.size:raise RecoveryError('Linked archive entry is incomplete.')
    if set(files)!=set(LIMITS):raise RecoveryError('Linked archive is missing an expected file.')
    manifest=json.loads(files['manifest.json'])
    if (manifest.get('schema')!=2 or manifest.get('network')!='testnet' or manifest.get('sourceRevision')!=recovery.REVISION
            or manifest.get('restoreRequiresDisabledWorkerAndFreshCommandWindow') is not True
            or set(manifest.get('fileSha256',{}))!=set(LIMITS)-{'manifest.json'}):raise RecoveryError('Linked archive manifest differs from the expected Testnet backup.')
    for name,expected in manifest['fileSha256'].items():
        if not isinstance(expected,str) or not hmac.compare_digest(hashlib.sha256(files[name]).hexdigest(),expected):raise RecoveryError('Linked archive file checksum failed.')
    config=tomllib.loads(files['zallet.toml'].decode())
    if config.get('consensus',{}).get('network')!='test' or config.get('external',{}).get('broadcast') is not False or config.get('keystore',{}).get('require_backup') is not True:
        raise RecoveryError('Linked wallet is missing its Testnet or recovery guards.')
    return files,manifest


def verify_bindings(files,manifest):
    with closing(database(files['app.db'])) as app,closing(database(files['broker.db'])) as broker:
        rows=app.execute("SELECT user_id,account,address,status FROM tip_wallets WHERE network='testnet' ORDER BY account").fetchall()
        linked=broker.execute("SELECT account FROM account_intents WHERE phase='created' ORDER BY account").fetchall()
        enabled=app.execute("SELECT COUNT(*) FROM tip_profiles WHERE network='testnet' AND enabled!=0").fetchone()[0]
    if (not 2<=len(rows)<=16 or len(rows)!=manifest.get('walletAccounts') or enabled
            or [r[1] for r in rows]!=[r[0] for r in linked] or any(r[3]!='ready' for r in rows)
            or len({r[0] for r in rows})!=len(rows)):
        raise RecoveryError('Linked account snapshots did not agree or automatic tips were already enabled.')
    account_hash=hashlib.sha256('\n'.join(r[1] for r in rows).encode()).hexdigest()
    binding_hash=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()
    if account_hash!=manifest.get('accountsSha256') or binding_hash!=manifest.get('bindingsSha256'):
        raise RecoveryError('Linked account mapping checksums failed.')
    return len(rows),account_hash,binding_hash


def verify(age,archive,identity,expected,folder):
    if archive.is_symlink() or identity.is_symlink() or not re.fullmatch(r'[a-fA-F0-9]{64}',expected):raise RecoveryError('Expected the existing local identity and encrypted linked backup.')
    with archive.open('rb') as file:digest=hashlib.file_digest(file,'sha256').hexdigest()
    if not hmac.compare_digest(digest,expected.lower()):raise RecoveryError('Linked backup checksum did not match the server report.')
    raw=recovery.checked_decrypt(age,['--identity',str(identity),str(archive)])
    files,manifest=read(raw);count,account_hash,binding_hash=verify_bindings(files,manifest)
    wallet=recovery.read_database(files['wallet.db'],manifest)
    if not wallet['confirmed'] or wallet['accounts']!=count:raise RecoveryError('Native wallet backup confirmation or account count differs.')
    phrase=recovery.decrypt_inner(age,files['encryption-identity.txt'],wallet['encryptedSeed'],folder)
    words=recovery.verify_words(phrase,phrase,wallet['fingerprint']);words.clear()
    return {'network':'testnet','encryptedBackupSha256':digest,'linkedBackupDecryptionVerified':True,
            'walletDatabaseIntegrityVerified':True,'operatorSeedRecoveryVerified':True,'nativeBackupConfirmed':True,
            'walletAccounts':count,'accountMappingsVerified':True,'accountsSha256':account_hash,'bindingsSha256':binding_hash,
            'nativeWalletRestoreVerified':False,'changesServer':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--sha256',required=True);args=parser.parse_args()
    if os.name!='nt':raise RecoveryError('Run locally in Windows PowerShell, never on the VPS.')
    folder=Path(os.environ['LOCALAPPDATA'])/'ZecTipBotRecovery'
    age=Path(os.environ['LOCALAPPDATA'])/'Microsoft/WinGet/Packages/FiloSottile.age_Microsoft.Winget.Source_8wekyb3d8bbwe/age/age.exe'
    if not age.is_file() or not folder.is_dir() or folder.is_symlink():raise RecoveryError('Expected the installed age and existing private recovery directory.')
    report=verify(age,folder/NAME,folder/'backup-key.txt',args.sha256,folder)
    receipt=folder/'linked-wallets-verification.json'
    if receipt.is_symlink():raise RecoveryError('Verification receipt must not be a symbolic link.')
    temporary=folder/('linked-verification-'+uuid.uuid4().hex+'.tmp')
    with temporary.open('x',encoding='utf-8') as file:json.dump(report,file,indent=2)
    os.replace(temporary,receipt)
    print(json.dumps(report,indent=2))
    print('Share only this status report. Private keys and recovery words were not displayed.')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'linkedBackupCheck':'failed','reason':str(error) if isinstance(error,RecoveryError) else type(error).__name__}))
        raise SystemExit(1)
