#!/usr/bin/env python3
"""Run Zallet's native backup quiz interactively, without echoing answers."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

VERIFICATION = Path('/root/recovery-verification.json')
UNIT = 'zec-tip-bot-wallet-testnet.service'


class ConfirmationError(Exception):
    pass


def load_helper(path, name):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ConfirmationError('Expected a protected root-owned helper.')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_verification(document, state):
    expected = {'network': 'testnet', 'encryptedArchiveChecksumMatches': True,
        'archiveFilesVerified': 5, 'walletDatabaseIntegrityVerified': True,
        'operatorSeedRecoveryVerified': True, 'recoveredSeedMatchesWallet': True,
        'recoveryWordCount': 24, 'walletAccountsInSnapshot': 0,
        'changesServer': False, 'operatorRecordedWordsThisRun': True,
        'encryptedBackupSha256': state.get('archiveSha256')}
    if not isinstance(document, dict) or not isinstance(expected['encryptedBackupSha256'], str):
        raise ConfirmationError('Expected a completed local recovery verification report.')
    for key, value in expected.items():
        if type(document.get(key)) is not type(value) or document.get(key) != value:
            raise ConfirmationError('Local recovery verification is incomplete or belongs to another archive.')


@contextmanager
def hidden_answers(terminal=None, stream=None):
    if terminal is None:
        import termios
        terminal = termios
    stream = sys.stdin if stream is None else stream
    descriptor = stream.fileno()
    original = terminal.tcgetattr(descriptor)
    hidden = original.copy()
    hidden[3] = (hidden[3] & ~terminal.ECHO) | terminal.ECHONL
    try:
        terminal.tcsetattr(descriptor, terminal.TCSANOW, hidden)
        yield
    finally:
        terminal.tcsetattr(descriptor, terminal.TCSANOW, original)


def run_native_quiz(prepared, user):
    with hidden_answers():
        # Inherit the operator's terminal. Answers are never argv, environment,
        # captured output, a receipt, a shell-history entry or a journal log.
        result = subprocess.run([str(prepared.BIN), '--datadir', str(prepared.DATA), 'confirm-backup'],
            timeout=300, cwd=prepared.DATA, user=user.pw_uid, group=user.pw_gid,
            extra_groups=[], umask=0o077,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(prepared.DATA), 'LANG': 'C.UTF-8'})
    return result.returncode


def record_native_confirmation(prepared, state, expected_fingerprint):
    after = prepared.metadata(prepared.DATA / 'wallet.db')
    if (after['accounts'] != 0 or after['seeds'] != 1 or not after['confirmed']
            or after['fingerprint'] != expected_fingerprint):
        raise ConfirmationError('Native backup confirmation is not complete. No accounts or transfers were created.')
    state.update(phase='native-backup-confirmed', nativeBackupConfirmed=True,
                 localSeedRecoveryVerified=True, nativeWalletRestoreVerified=False,
                 locallyVerifiedArchiveSha256=state['archiveSha256'],
                 nativeBackupConfirmedUtc=datetime.now(timezone.utc).isoformat())
    prepared.save_state(state)
    return {'network': 'testnet', 'backupConfirmedByNativeWallet': True,
            'offServerSeedRecoveryVerified': True, 'nativeWalletRestoreVerified': False,
            'accountsCreated': False, 'depositsEnabled': False, 'spendingEnabled': False}


def main():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ConfirmationError('Run this in your existing interactive VPS root terminal.')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ConfirmationError('Run directly in your private VPS terminal; do not use a background service or log pipe.')
    import signal
    def interrupted(signum, frame):
        raise KeyboardInterrupt()
    # Make normal SSH hangup/termination unwind the terminal and service contexts.
    signal.signal(signal.SIGHUP, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    pool = load_helper(Path('/root/fix-testnet-wallet-pool.py'), 'confirmation_pool')
    prepared = load_helper(Path('/root/prepare-testnet-recovery.py'), 'confirmation_prepared')
    base = pool.load_helper('build-testnet-nu7-candidate.py', 'confirmation_build')
    base.guard()
    check = pool.load_helper('check-testnet-wallet.py', 'confirmation_check')
    pool.verify_testnet(check)
    pool.service_capacity()
    activation = pool.load_helper('activate-testnet-nu7-node.py', 'confirmation_activation')
    record = base.validate_manifest(activation.ARTIFACTS)
    activation.require_build_metadata(record)
    with prepared.BIN.open('rb') as file:
        if hashlib.file_digest(file, 'sha256').hexdigest() != record['binaries']['zallet-zebra']:
            raise ConfirmationError('Installed wallet differs from the verified build.')
    import fcntl
    import pwd
    user = pwd.getpwnam('zectip-chain')
    state = prepared.private_state()
    if not state or not state.get('archive') or not state.get('archiveSha256'):
        raise ConfirmationError('The encrypted operator backup has not been prepared.')
    receipt_info = VERIFICATION.lstat()
    if (not stat.S_ISREG(receipt_info.st_mode) or receipt_info.st_uid != 0
            or receipt_info.st_mode & 0o022 or receipt_info.st_size > 8192):
        raise ConfirmationError('Expected the uploaded non-secret local verification report.')
    document = json.loads(VERIFICATION.read_text())
    validate_verification(document, state)
    archive = Path(state['archive'])
    if archive.parent != prepared.ROOT or archive.is_symlink():
        raise ConfirmationError('Unexpected encrypted archive location.')
    prepared.protected_file(archive, 0)
    if prepared.file_digest(archive) != state['archiveSha256']:
        raise ConfirmationError('Encrypted server archive does not match the locally verified copy.')
    lock_path = prepared.ROOT / 'workflow.lock'
    prepared.protected_file(lock_path, 0)
    with lock_path.open('r+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfirmationError('Another operator recovery step is already running.') from None
        state = prepared.private_state()
        if not state:
            raise ConfirmationError('Protected recovery state is unavailable.')
        validate_verification(document, state)
        before = prepared.metadata(prepared.DATA / 'wallet.db')
        if before['accounts'] != 0 or before['seeds'] != 1 or before['fingerprint'] != state.get('fingerprint'):
            raise ConfirmationError('The operator wallet differs from the verified empty-wallet backup.')
        if before['confirmed']:
            report = record_native_confirmation(prepared, state, before['fingerprint'])
            report['walletRestarted'] = False
        else:
            print('Read each requested word position from your recorded backup. Typed answers will be hidden.', flush=True)
            with prepared.paused_wallet(pool):
                exit_code = run_native_quiz(prepared, user)
                if exit_code != 0:
                    raise ConfirmationError('Native backup quiz did not complete. The wallet will restart; no accounts were created.')
                report = record_native_confirmation(prepared, state, before['fingerprint'])
            report['walletRestarted'] = True
    print(json.dumps(report, indent=2))
    print('Share only this final status report. Keep your recovery words private.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nBackup confirmation cancelled. If the wallet was paused, restart was attempted.', file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        print('Backup confirmation stopped: ' + (str(error) if isinstance(error, ConfirmationError)
                                                else type(error).__name__), file=sys.stderr)
        sys.exit(1)
