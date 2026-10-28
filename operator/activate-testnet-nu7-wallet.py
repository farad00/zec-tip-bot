#!/usr/bin/env python3
"""Connect a new empty NU7 Testnet wallet; never create a spending seed or account."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time

DATA = Path('/var/lib/zec-tip-bot-wallet-nu7-testnet')
RUNTIME = Path('/opt/zec-tip-bot-chain/nu7-runtime-8c5a93b-r5')
BIN = RUNTIME / 'zallet-zebra'
UNIT = 'zec-tip-bot-wallet-testnet.service'
DROPIN = Path('/etc/systemd/system/zec-tip-bot-wallet-testnet.service.d/30-nu7-backend.conf')
PUBLISHER = Path('/opt/zec-tip-bot-chain/wallet-health-publisher.py')
PUBLISHER_INPUT = Path('/root/wallet-health-publisher.py')
PUBLISHER_SHA = 'ec412d438014ea9ebf6eab008a78eab4ebe5572ca724ab5fc55775692410673e'
HEALTH_UNIT = 'zec-tip-bot-wallet-health.service'
HEALTH_DROPIN = Path('/etc/systemd/system/zec-tip-bot-wallet-health.service.d/30-nu7-backend.conf')
USER = 'zectip-chain'


def load_helper(name, filename):
    path = Path('/root') / filename
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022):
        raise ValueError('Expected a root-owned setup helper without public write access.')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configuration(cookie):
    return f'''# Empty hosted-wallet staging. TESTNET ONLY. No spending seed/accounts.
backend = "zebra"
[builder]
trusted_confirmations = 3
untrusted_confirmations = 10
[builder.limits]
[consensus]
network = "test"
[database]
[features]
as_of_version = "0.1.0-beta.3"
[features.deprecated]
[features.experimental]
[external]
broadcast = false
[indexer]
validator_address = "127.0.0.1:18232"
validator_cookie_path = "{cookie.as_posix()}"
[indexer.read_state_service]
grpc_address = "127.0.0.1:18230"
zebra_state_path = "/var/lib/zec-tip-bot-chain-testnet/cache"
[keystore]
require_backup = true
[note_management]
[rpc]
bind = ["127.0.0.1:28232"]
async_operation_limit = 0
[sync]
recover_batch_size = 1000
'''


def wallet_dropin():
    return f'''# New empty wallet; original legacy wallet directory is preserved.
[Service]
Environment=HOME={DATA.as_posix()}
WorkingDirectory={DATA.as_posix()}
ExecStart=
ExecStart={BIN.as_posix()} --datadir {DATA.as_posix()} start
ReadWritePaths=
ReadWritePaths={DATA.as_posix()}
'''


def health_dropin():
    return '[Service]\nEnvironment=ZEC_WALLET_HEALTH_BACKEND=zebra\n'


def marker():
    return {'schema': 1, 'network': 'testnet', 'backend': 'zebra',
            'sourceRevision': '8c5a93ba9d68ad78d199c3624413c0139781b837',
            'phase': 'initialized_empty_wallet'}


def initialization_commands():
    identity = DATA / 'encryption-identity.txt'
    return [[str(BIN), '--datadir', str(DATA), 'generate-encryption-identity', '--output', str(identity)],
            [str(BIN), '--datadir', str(DATA), 'init-wallet-encryption']]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def diagnostic_sections(check, journal, setup, credentials):
    # Each source gets its own bound: old initialization migrations must never
    # crowd the current service startup/failure out of the diagnostic report.
    result = {'walletJournal': check.safe_excerpt(journal, credentials)}
    if setup:
        result['walletSetupDiagnostics'] = check.safe_excerpt(setup, credentials)
    return result


def status(check, activation):
    report = activation.status(check)
    report['walletService'] = subprocess.run(['systemctl', 'is-active', UNIT], capture_output=True,
                                              text=True).stdout.strip()
    report['walletBackend'] = 'zebra'
    report['walletRpc'] = 'unavailable'
    report['spendingEnabled'] = False
    report['spendingAccountsCreatedBySetup'] = False
    report['currentTestnetTipVerified'] = False
    if not DROPIN.exists() or DROPIN.read_text() != wallet_dropin():
        report['walletBackendUpgradePending'] = True
        return report
    report['walletBackendUpgradePending'] = False
    try:
        cookie = check.cookie_file(DATA)
        discovery = check.rpc(28232, cookie, 'rpc.discover')
        report['walletCapabilities'] = check.capability_report(discovery)
        report['walletRpc'] = 'connected'
    except (ValueError, OSError, TimeoutError) as error:
        report['walletReadErrorCategory'] = type(error).__name__
        return report
    try:
        wallet = check.rpc(28232, cookie, 'getwalletstatus')
        if not isinstance(wallet, dict):
            raise ValueError('Unexpected wallet status response.')
        report['walletStatusRead'] = 'available'
        if isinstance(wallet.get('locked'), bool):
            report['walletLockedForSync'] = wallet['locked']
        for key, output in (('node_tip', 'walletNodeHeight'), ('wallet_tip', 'walletScanHeight')):
            tip = wallet.get(key)
            height = tip.get('height') if isinstance(tip, dict) else None
            if type(height) is int and 0 <= height <= 0xffff_ffff:
                report[output] = height
    except (ValueError, OSError, TimeoutError) as error:
        report['walletStatusRead'] = 'unavailable'
        report['walletStatusErrorCategory'] = type(error).__name__
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--diagnose', action='store_true')
    args = parser.parse_args()
    activation = load_helper('node_activation', 'activate-testnet-nu7-node.py')
    base = activation.load_helper('build_base', 'build-testnet-nu7-candidate.py')
    base.guard()
    check = load_helper('wallet_check', 'check-testnet-wallet.py')
    if args.status or args.diagnose:
        report = status(check, activation)
        if args.diagnose:
            credentials = []
            for directory in (check.NODE_RPC, DATA):
                try:
                    credentials.append(check.cookie_file(directory).read_text().strip())
                except (ValueError, OSError):
                    pass
            journal = run('journalctl', '-u', UNIT, '-n', '80', '--no-pager', '--output=cat', capture_output=True).stdout
            setup_log = DATA / 'setup.log'
            setup = ''
            if setup_log.is_file() and not setup_log.is_symlink():
                with setup_log.open('rb') as file:
                    file.seek(max(0, setup_log.stat().st_size - 16000))
                    setup = file.read(16000).decode(errors='replace')
            report.update(diagnostic_sections(check, journal, setup, credentials))
        print(json.dumps(report, indent=2))
        return
    # Observe node readiness BEFORE touching wallet material or services.
    node = activation.status(check)
    if (node.get('candidateProcess') is not True or node.get('nodeRpc') != 'connected'
            or node.get('localIndexerPortReachable') is not True):
        print(json.dumps(node, indent=2))
        print('The node is not ready for the new wallet. No new wallet files or services were changed.')
        if node.get('nodeService') != 'active' or node.get('candidateProcess') is not True:
            print('Inspect the node: python3 /root/activate-testnet-nu7-node.py --diagnose')
        else:
            print('Run this same setup command again once node RPC and its local indexer are reachable.')
        return
    build = base.validate_manifest(activation.ARTIFACTS)
    activation.require_build_metadata(build)
    for name in ('zebrad', 'zallet', 'zallet-zebra'):
        activation.install_binary(activation.ARTIFACTS / name, RUNTIME / name, build['binaries'][name])
    if PUBLISHER_INPUT.is_symlink() or not PUBLISHER_INPUT.is_file():
        raise ValueError('Upload the updated wallet-health-publisher.py alongside this helper.')
    payload = PUBLISHER_INPUT.read_bytes()
    if hashlib.sha256(payload).hexdigest() != PUBLISHER_SHA:
        raise ValueError('The status publisher does not match its expected SHA256.')
    previous = load_helper('legacy_wallet', 'prepare-testnet-wallet.py')
    if previous.UNIT_PATH.is_symlink() or previous.UNIT_PATH.read_text() != previous.service_unit():
        raise ValueError('Existing wallet service differs from the prepared original.')
    for path, content in ((DROPIN, wallet_dropin()), (HEALTH_DROPIN, health_dropin())):
        if path.is_symlink() or (path.exists() and path.read_text() != content):
            raise ValueError('An existing backend service override differs; it was preserved.')
    active = subprocess.run(['systemctl', 'is-active', UNIT], capture_output=True, text=True).stdout.strip()
    if active == 'active':
        if DROPIN.exists() and DROPIN.read_text() == wallet_dropin():
            print(json.dumps(status(check, activation), indent=2))
            print('The prepared backend is already running. No wallet was reinitialized.')
            return
        raise ValueError('The old wallet is still active. Complete the node activation first.')
    with socket.socket() as connection:
        connection.settimeout(1)
        if connection.connect_ex(('127.0.0.1', 28232)) == 0:
            raise ValueError('Wallet RPC port is already occupied by another process.')
    import pwd
    user = pwd.getpwnam(USER)
    cookie = check.cookie_file(check.NODE_RPC)
    config = configuration(cookie)
    if DATA.is_symlink():
        raise ValueError('The new wallet directory must not be a symbolic link.')
    if DATA.exists():
        if ((DATA / '.setup.json').is_symlink() or json.loads((DATA / '.setup.json').read_text()) != marker()
                or (DATA / 'zallet.toml').read_text() != config):
            raise ValueError('Existing new-wallet setup is incomplete or differs; it was preserved.')
    else:
        DATA.mkdir(mode=0o700)
        os.chown(DATA, user.pw_uid, user.pw_gid)
        activation.write_new(DATA / 'zallet.toml', config, 0o600)
        os.chown(DATA / 'zallet.toml', user.pw_uid, user.pw_gid)
        # This initializes only an encryption identity and an empty database.
        # No mnemonic, spending account, receiving address or transaction is made.
        with (DATA / 'setup.log').open('x') as log:
            os.chmod(log.name, 0o600)
            for command in initialization_commands():
                result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, timeout=60, cwd=DATA, user=user.pw_uid,
                    group=user.pw_gid, extra_groups=[], umask=0o077,
                    env={'PATH': '/usr/bin:/bin', 'HOME': str(DATA), 'LANG': 'C.UTF-8'})
                if result.returncode:
                    raise ValueError('Empty-wallet initialization failed. Run this helper with --diagnose.')
        activation.write_new(DATA / '.setup.json', json.dumps(marker(), indent=2) + '\n', 0o600)
    directory = DATA.stat()
    identity = (DATA / 'encryption-identity.txt').lstat()
    if directory.st_uid != user.pw_uid or stat.S_IMODE(directory.st_mode) != 0o700:
        raise ValueError('The new wallet directory must be private to its service user.')
    if (not stat.S_ISREG(identity.st_mode) or identity.st_uid != user.pw_uid
            or stat.S_IMODE(identity.st_mode) != 0o600):
        raise ValueError('The encryption identity must be a private regular file.')
    for path, content in ((DROPIN, wallet_dropin()), (HEALTH_DROPIN, health_dropin())):
        if path.parent.is_symlink():
            raise ValueError('Service override directory must not be a symbolic link.')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        activation.write_new(path, content, 0o644)
    if PUBLISHER.is_symlink() or not PUBLISHER.is_file():
        raise ValueError('The existing sync publisher was not found.')
    backup = RUNTIME / 'legacy-wallet-health-publisher.py'
    if not backup.exists():
        activation.write_new(backup, PUBLISHER.read_text(), 0o600)
    temporary = PUBLISHER.with_name('wallet-health-publisher.nu7-new.py')
    activation.write_new(temporary, payload.decode(), 0o644)
    os.replace(temporary, PUBLISHER)
    run('systemd-analyze', 'verify', str(previous.UNIT_PATH), capture_output=True)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', UNIT, capture_output=True)
    run('systemctl', 'restart', HEALTH_UNIT)
    time.sleep(3)
    print(json.dumps(status(check, activation), indent=2))
    print('New empty Testnet backend prepared. Original wallet directory was left untouched.')
    print('No recovery phrase, spending account, deposit address or transfer was created.')
    print('Next: python3 /root/activate-testnet-nu7-wallet.py --status')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print('Wallet activation stopped: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        print('Existing wallet material was preserved; no spending operation was requested.', file=sys.stderr)
        sys.exit(1)
