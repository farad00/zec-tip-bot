#!/usr/bin/env python3
"""Bundle only encrypted enrollment snapshots for off-server copying."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import zipfile

ROOT=Path('/var/lib/zec-tip-bot-wallet-broker/enrollment-backups')
TARGET=Path('/root/zec-tip-bot-testnet-recovery/enrollment-backups.zip')


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the VPS root terminal.')
    os.umask(0o077)
    with closing(sqlite3.connect('file:/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite?mode=ro',uri=True)) as db:
        rows=db.execute("SELECT archive,sha FROM enrollment_bindings WHERE phase='active' ORDER BY account").fetchall()
    if not 1<=len(rows)<=16:raise ValueError('No active enrollment snapshots are ready to copy.')
    manifest={};temporary=TARGET.with_name('enrollment-backups.pending.zip')
    if temporary.exists():raise ValueError('An unfinished export exists; it was preserved.')
    try:
        with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_STORED) as archive:
            for name,sha in rows:
                if not __import__('re').fullmatch(r'[a-f0-9-]{36}\.tar\.age',name):raise ValueError('Invalid snapshot name.')
                path=ROOT/name;info=path.lstat()
                if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_mode&0o077:raise ValueError('Snapshot permissions differ.')
                with path.open('rb') as file:digest=hashlib.file_digest(file,'sha256').hexdigest()
                if digest!=sha:raise ValueError('Encrypted snapshot checksum did not match.')
                archive.write(path,name);manifest[name]=sha
            archive.writestr('manifest.json',json.dumps({'network':'testnet','encryptedFiles':manifest},sort_keys=True))
        with temporary.open('rb') as file:os.fsync(file.fileno())
        os.replace(temporary,TARGET)
    finally:
        if temporary.exists():temporary.unlink()
    with TARGET.open('rb') as file:sha=hashlib.file_digest(file,'sha256').hexdigest()
    print(json.dumps({'network':'testnet','encryptedEnrollmentBundle':str(TARGET),'sha256':sha,
        'snapshots':len(rows),'privateKeysOrPlaintextIncluded':False,'offServerVerified':False,
        'paymentsOrSettingsChanged':False},indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:print('Enrollment export stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__));raise SystemExit(1)
