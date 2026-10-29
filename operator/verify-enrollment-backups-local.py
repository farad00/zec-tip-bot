#!/usr/bin/env python3
"""Human-run local verification. Private snapshots are decrypted only in memory."""
from contextlib import closing
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import uuid
import zipfile

spec=importlib.util.spec_from_file_location('linked_recovery',Path(__file__).with_name('verify-linked-wallets-local.py'))
linked=importlib.util.module_from_spec(spec);spec.loader.exec_module(linked)
LIMITS={'wallet.db':512*1024*1024,'broker.db':32*1024*1024,'binding.json':8192,'manifest.json':8192}


def snapshot(raw):
    if len(raw)>sum(LIMITS.values())+65536:raise ValueError('Snapshot exceeds the verification size bound.')
    files={}
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
        for entry in archive:
            if entry.name not in LIMITS or entry.name in files or not entry.isfile() or not 0<entry.size<=LIMITS[entry.name]:raise ValueError('Unexpected enrollment snapshot entry.')
            with archive.extractfile(entry) as file:files[entry.name]=file.read(entry.size+1)
            if len(files[entry.name])!=entry.size:raise ValueError('Truncated enrollment snapshot.')
    if set(files)!=set(LIMITS):raise ValueError('Incomplete enrollment snapshot.')
    manifest=json.loads(files['manifest.json'])
    if manifest.get('network')!='testnet' or manifest.get('containsEncryptionIdentity') is not False or set(manifest.get('files',{}))!=set(files)-{'manifest.json'}:raise ValueError('Invalid enrollment manifest.')
    for name,sha in manifest['files'].items():
        if hashlib.sha256(files[name]).hexdigest()!=sha:raise ValueError('Enrollment snapshot checksum mismatch.')
    return files,json.loads(files['binding.json'])


def check_binding(files,binding,seed):
    if (binding.get('network')!='testnet' or not isinstance(binding.get('userId'),str) or not re.fullmatch(r'[0-9]{1,20}',binding['userId'])
            or type(binding.get('zip32_account_index')) is not int or not 0<=binding['zip32_account_index']<=0x7fffffff
            or binding.get('birthday_height')!=0 or type(binding.get('diversifier_index',0)) is not int
            or binding.get('diversifier_index',0) not in (0,4294967296)):raise ValueError('Invalid recovery binding metadata.')
    with closing(linked.database(files['broker.db'])) as db:
        intent=db.execute("SELECT label FROM account_intents WHERE account=? AND phase='created'",(binding['account'],)).fetchone()
        row=db.execute('SELECT user_id,label,address,metadata FROM enrollment_bindings WHERE account=?',(binding['account'],)).fetchone()
    if intent!=(binding['label'],) or not row or row[:3]!=(binding['userId'],binding['label'],binding['address']) or json.loads(row[3])!=binding:raise ValueError('Enrollment identity mapping did not match its snapshot.')
    with closing(linked.database(files['wallet.db'])) as db:
        seeds=db.execute('SELECT hex(hd_seed_fingerprint),backup_confirmed FROM ext_zallet_keystore_mnemonics').fetchall()
        if seeds!=[(seed.upper(),1)]:raise ValueError('Enrollment snapshot uses a different or unconfirmed seed.')
        if not db.execute('SELECT 1 FROM accounts WHERE uuid=?',(uuid.UUID(binding['account']).bytes,)).fetchone():raise ValueError('Linked native wallet account is missing.')
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--sha256',required=True);args=parser.parse_args()
    if os.name!='nt':raise ValueError('Run on your PC in Windows PowerShell.')
    folder=Path(os.environ['LOCALAPPDATA'])/'ZecTipBotRecovery';bundle=folder/'enrollment-backups.zip'
    age=Path(os.environ['LOCALAPPDATA'])/'Microsoft/WinGet/Packages/FiloSottile.age_Microsoft.Winget.Source_8wekyb3d8bbwe/age/age.exe'
    identity=folder/'backup-key.txt'
    if not re.fullmatch(r'[a-fA-F0-9]{64}',args.sha256) or bundle.is_symlink() or identity.is_symlink():raise ValueError('Expected the existing recovery key and encrypted export.')
    with bundle.open('rb') as file:sha=hashlib.file_digest(file,'sha256').hexdigest()
    if sha!=args.sha256.lower():raise ValueError('Export checksum differs from the VPS report.')
    original=folder/linked.NAME
    with original.open('rb') as file:old_sha=hashlib.file_digest(file,'sha256').hexdigest()
    if old_sha!='4308f3d7dc3bed9bc2f095454e13381e3cf2634bf43c3f64f525765767cf21e7':raise ValueError('Expected the previously verified original linked backup.')
    original_files,original_manifest=linked.read(linked.recovery.checked_decrypt(age,['--identity',str(identity),str(original)]))
    seed=original_manifest['seedFingerprintHex'];original_files.clear()
    count=0
    with zipfile.ZipFile(bundle) as zipped:
        names=zipped.namelist()
        if len(names)!=len(set(names)) or not 2<=len(names)<=17 or 'manifest.json' not in names:raise ValueError('Invalid encrypted export entries.')
        manifest=json.loads(zipped.read('manifest.json'))
        if manifest.get('network')!='testnet' or set(manifest.get('encryptedFiles',{}))!=set(names)-{'manifest.json'}:raise ValueError('Invalid export manifest.')
        for name,expected in manifest['encryptedFiles'].items():
            if not re.fullmatch(r'[a-f0-9-]{36}\.tar\.age',name) or zipped.getinfo(name).file_size>sum(LIMITS.values())+1024*1024:raise ValueError('Invalid encrypted snapshot entry.')
            encrypted=zipped.read(name)
            if hashlib.sha256(encrypted).hexdigest()!=expected:raise ValueError('Encrypted snapshot checksum mismatch.')
            result=subprocess.run([str(age),'--decrypt','--identity',str(identity)],input=encrypted,capture_output=True,timeout=180)
            if result.returncode:raise ValueError('Enrollment snapshot decryption failed.')
            files,binding=snapshot(result.stdout);check_binding(files,binding,seed);files.clear();count+=1
    print(json.dumps({'network':'testnet','bundleSha256':sha,'offServerEncryptedSnapshotsVerified':count,
        'accountBindingsVerified':True,'nativeDatabaseIntegrityVerified':True,'sameConfirmedSeed':True,
        'privateKeysAddressesOrIdentitiesPrinted':False,'changesServer':False,'nativeWalletRestoreVerified':False},indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:print(json.dumps({'enrollmentBackupVerification':'failed','reason':str(error) if isinstance(error,ValueError) else type(error).__name__}));raise SystemExit(1)
