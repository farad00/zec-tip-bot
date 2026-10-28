#!/usr/bin/env python3
"""Prepare an encrypted operator backup for the empty Testnet wallet. Never create accounts."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tarfile
import uuid

DATA = Path('/var/lib/zec-tip-bot-wallet-nu7-testnet')
ROOT = Path('/root/zec-tip-bot-testnet-recovery')
RECEIPT = ROOT / 'recovery-state.json'
UNIT = 'zec-tip-bot-wallet-testnet.service'
BIN = Path('/opt/zec-tip-bot-chain/nu7-runtime-8c5a93b-r5/zallet-zebra')
REVISION = '8c5a93ba9d68ad78d199c3624413c0139781b837'


def protected_file(path, owner=None):
    item = path.lstat()
    if not stat.S_ISREG(item.st_mode) or item.st_mode & 0o077:
        raise ValueError('Expected a private regular recovery file.')
    if owner is not None and item.st_uid != owner:
        raise ValueError('Unexpected recovery file owner.')


def metadata(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected the existing wallet database.')
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        accounts = db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
        seeds = db.execute('SELECT hex(hd_seed_fingerprint), backup_confirmed '
                           'FROM ext_zallet_keystore_mnemonics').fetchall()
    if len(seeds) > 1 or any(len(row[0]) != 64 or row[1] not in (0, 1) for row in seeds):
        raise ValueError('Expected at most one operator seed; manual reconciliation required.')
    return {'accounts': accounts, 'seeds': len(seeds),
            'fingerprint': seeds[0][0] if seeds else None,
            'confirmed': bool(seeds and seeds[0][1])}


def private_state():
    if not RECEIPT.exists():
        return None
    protected_file(RECEIPT, 0)
    result = json.loads(RECEIPT.read_text())
    if result.get('network') != 'testnet' or result.get('sourceRevision') != REVISION:
        raise ValueError('An existing recovery record differs; it was preserved.')
    return result


def save_state(state):
    temporary = ROOT / ('state-' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as file:
        os.chmod(temporary, 0o600)
        json.dump(state, file, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, RECEIPT)


def native(arguments, user, output=None):
    result = subprocess.run([str(BIN), '--datadir', str(DATA), *arguments],
        stdin=subprocess.DEVNULL, stdout=output if output is not None else subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=660, cwd=DATA,
        user=user.pw_uid, group=user.pw_gid, extra_groups=[], umask=0o077,
        env={'PATH': '/usr/bin:/bin', 'HOME': str(DATA), 'LANG': 'C.UTF-8'})
    if result.returncode:
        # Never put a native wallet's raw output or command contents in a status report.
        report = {'nativeStep': arguments[0], 'exitCode': result.returncode,
                  'walletMaterialPreserved': True}
        raise ValueError(json.dumps(report))


def ensure_one_seed(user):
    before = metadata(DATA / 'wallet.db')
    state = private_state()
    if before['accounts'] != 0:
        raise ValueError('This bootstrap helper requires zero wallet accounts.')
    if state and state.get('fingerprint') and state['fingerprint'] != before['fingerprint']:
        raise ValueError('Operator seed differs from the protected recovery record.')
    if before['seeds']:
        if not state:
            raise ValueError('An existing seed has no matching recovery record; it was preserved.')
        # Reconcile a generation that completed before its helper was interrupted.
        state.update(phase='seed-prepared', fingerprint=before['fingerprint'])
        save_state(state)
        return state, False
    if state:
        raise ValueError('Earlier seed generation is uncertain; do not generate another seed blindly.')
    state = {'schema': 1, 'network': 'testnet', 'sourceRevision': REVISION,
             'phase': 'generating', 'fingerprint': None}
    # Persist the intent before mutation; an interrupted CLI is never blindly repeated.
    save_state(state)
    native(['generate-mnemonic'], user)
    after = metadata(DATA / 'wallet.db')
    if after['seeds'] != 1 or after['accounts'] != 0 or after['confirmed']:
        raise ValueError('Seed generation did not produce the expected unconfirmed empty wallet.')
    state.update(phase='seed-prepared', fingerprint=after['fingerprint'])
    save_state(state)
    return state, True


def restricted_tar_info(info):
    if not info.isfile():
        raise ValueError('Only fixed regular files may enter the recovery archive.')
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    info.mode = 0o600
    return info


def file_digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def validate_recipient(recipient):
    if not re.fullmatch(r'age1[0-9a-z]{58}', recipient):
        raise ValueError('Expected the public age recipient, never a private identity or passphrase.')
    # Let the installed age implementation validate the recipient checksum before
    # creating any spending seed or stopping services. Only empty public data enters it.
    result = subprocess.run(['/usr/bin/age', '--encrypt', '--recipient', recipient],
                            input=b'', stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
    if result.returncode:
        raise ValueError('The public recovery recipient was not accepted by age.')


def encrypt_archive(files, destination, manifest, recipient):
    if destination.exists() or destination.is_symlink():
        raise ValueError('An existing encrypted backup will not be overwritten.')
    partial = destination.with_suffix(destination.suffix + '.partial')
    # Only a PUBLIC recipient is present on the VPS. The corresponding recovery
    # identity is created and retained on the operator's own computer.
    with partial.open('xb') as output:
        os.chmod(partial, 0o600)
        process = subprocess.Popen(['/usr/bin/age', '--encrypt', '--recipient', recipient],
                                   stdin=subprocess.PIPE, stdout=output, stderr=subprocess.DEVNULL)
        try:
            assert process.stdin is not None
            with tarfile.open(fileobj=process.stdin, mode='w|') as archive:
                for name, path in files.items():
                    protected_file(path)
                    archive.add(path, arcname=name, recursive=False, filter=restricted_tar_info)
                content = json.dumps(manifest, indent=2).encode()
                info = tarfile.TarInfo('manifest.json')
                info.size = len(content)
                archive.addfile(restricted_tar_info(info), io.BytesIO(content))
            process.stdin.close()
            if process.wait(timeout=600) != 0:
                raise ValueError('Backup encryption did not complete; no complete backup was recorded.')
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            process.kill()
            process.wait()
            raise
    if partial.stat().st_size < 100:
        raise ValueError('Encrypted backup was unexpectedly small.')
    # Same private parent; exclusive final name was chosen for this operation.
    os.link(partial, destination)
    partial.unlink()
    with destination.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


@contextmanager
def paused_wallet(pool):
    try:
        pool.run('systemctl', 'stop', UNIT, timeout=45)
        yield
    finally:
        # Also attempt restoration if stopping failed midway. The node never stops.
        pool.run('systemctl', 'start', UNIT, timeout=45)


def prepare(user, pool, recipient):
    if not Path('/usr/bin/age').is_file():
        raise ValueError('Install the signed Ubuntu age package first: apt-get update && apt-get install -y age')
    validate_recipient(recipient)
    pool.service_capacity()  # Requires the expected running binary and dedicated cgroup.
    if ROOT.is_symlink():
        raise ValueError('Recovery directory must not be a symbolic link.')
    ROOT.mkdir(mode=0o700, exist_ok=True)
    item = ROOT.stat()
    if item.st_uid != 0 or stat.S_IMODE(item.st_mode) != 0o700:
        raise ValueError('Recovery directory must be root-owned with mode 0700.')
    import fcntl
    lock_path = ROOT / 'workflow.lock'
    if lock_path.is_symlink():
        raise ValueError('Recovery workflow lock must not be a symbolic link.')
    with lock_path.open('a') as lock:
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another recovery preparation is already running.') from None
        # Reject unrelated existing wallets before stopping any service.
        initial = metadata(DATA / 'wallet.db')
        if initial['accounts'] != 0 or (initial['seeds'] and not private_state()):
            raise ValueError('Expected the empty wallet or a seed previously created by this helper.')
        previous = private_state()
        if previous and previous.get('backupRecipient') not in (None, recipient):
            raise ValueError('The public backup recipient changed. Existing recovery material was preserved.')
        stage = DATA / ('.operator-backup-' + uuid.uuid4().hex)
        stage.mkdir(mode=0o700)
        os.chown(stage, user.pw_uid, user.pw_gid)
        print('Pausing only the wallet to prepare its operator recovery material...', flush=True)
        # If systemctl stop itself fails, do not proceed with offline CLI operations.
        with paused_wallet(pool):
            state, created = ensure_one_seed(user)
            exported = stage / 'mnemonic.age'
            with exported.open('xb') as file:
                os.chmod(exported, 0o600)
                os.chown(exported, user.pw_uid, user.pw_gid)
                native(['export-mnemonic', '--armor'], user, output=file)
            if not exported.read_bytes().startswith(b'-----BEGIN AGE ENCRYPTED FILE-----'):
                raise ValueError('Native mnemonic export was not the expected encrypted format.')
            snapshot = stage / 'wallet.db'
            native(['backup', str(snapshot)], user)
            protected_file(snapshot, user.pw_uid)
            if metadata(snapshot)['fingerprint'] != state['fingerprint']:
                raise ValueError('Database snapshot does not match the recorded operator seed.')
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            destination = ROOT / ('testnet-recovery-' + stamp + '-' + uuid.uuid4().hex[:8] + '.tar.age')
            files = {'wallet.db': snapshot, 'mnemonic.age': exported,
                     'encryption-identity.txt': DATA / 'encryption-identity.txt',
                     'zallet.toml': DATA / 'zallet.toml'}
            manifest = {'schema': 1, 'network': 'testnet', 'sourceRevision': REVISION,
                        'seedFingerprintHex': state['fingerprint'], 'walletAccounts': 0,
                        'createdUtc': datetime.now(timezone.utc).isoformat(),
                        'fileSha256': {name: file_digest(path)
                                       for name, path in files.items()}}
            print('Encrypting the complete archive for your off-server recovery key...', flush=True)
            digest = encrypt_archive(files, destination, manifest, recipient)
            state.update(phase='encrypted-backup-prepared', archive=str(destination), archiveSha256=digest,
                         backupRecipient=recipient)
            save_state(state)
            return {'network': 'testnet', 'operatorSeedCreatedThisRun': created,
                    'encryptedBackup': str(destination), 'encryptedBackupSha256': digest,
                    'backupConfirmed': metadata(snapshot)['confirmed'], 'offServerRecoveryVerified': False,
                    'accountsCreated': False, 'depositsEnabled': False, 'spendingEnabled': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--recipient', help='Public age recipient generated on the operator computer.')
    args = parser.parse_args()
    if args.prepare and not args.recipient:
        raise ValueError('Supply only your public recovery recipient with --recipient.')
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Run this in the existing VPS root terminal.')
    import importlib.util
    path = Path('/root/fix-testnet-wallet-pool.py')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Expected the protected pool setup helper.')
    spec = importlib.util.spec_from_file_location('recovery_pool', path)
    pool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pool)
    base = pool.load_helper('build-testnet-nu7-candidate.py', 'recovery_build')
    base.guard()
    check = pool.load_helper('check-testnet-wallet.py', 'recovery_read_check')
    pool.verify_testnet(check)
    activation = pool.load_helper('activate-testnet-nu7-node.py', 'recovery_activation')
    record = base.validate_manifest(activation.ARTIFACTS)
    activation.require_build_metadata(record)
    with BIN.open('rb') as file:
        if hashlib.file_digest(file, 'sha256').hexdigest() != record['binaries']['zallet-zebra']:
            raise ValueError('Installed wallet binary differs from the verified build.')
    import pwd
    user = pwd.getpwnam('zectip-chain')
    for name in ('wallet.db', 'encryption-identity.txt', 'zallet.toml'):
        protected_file(DATA / name, user.pw_uid)
    if args.prepare:
        result = prepare(user, pool, args.recipient)
    else:
        current = metadata(DATA / 'wallet.db')
        result = {'network': 'testnet', 'walletAccounts': current['accounts'],
                  'operatorSeedCount': current['seeds'], 'backupConfirmed': current['confirmed'],
                  'changesServices': False, 'createsKeysOrAccounts': False}
    print(json.dumps(result, indent=2))
    if args.prepare:
        print('Next: copy the encrypted archive off the server, verify recovery privately, then complete native backup confirmation.')
        print('Do not paste recovery words or private encryption keys into chat.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        print('Recovery preparation stopped: ' + (str(error) if isinstance(error, ValueError)
                                                else type(error).__name__), file=sys.stderr)
        sys.exit(1)
