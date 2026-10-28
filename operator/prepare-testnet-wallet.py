#!/usr/bin/env python3
"""Start a persistent, empty Testnet wallet. No seed or spending accounts are created."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

DATA = Path('/var/lib/zec-tip-bot-wallet-testnet')
UNIT_PATH = Path('/etc/systemd/system/zec-tip-bot-wallet-testnet.service')
UNIT = UNIT_PATH.name
BIN = Path('/opt/zec-tip-bot-chain/zallet-v0.1.0-beta.3/zallet-zaino')
USER = 'zectip-chain'


def configuration(cookie):
    return f'''# Hosted wallet staging: TESTNET ONLY. No seed/accounts created by setup.
backend = "zaino"
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
[keystore]
require_backup = true
[note_management]
[rpc]
bind = ["127.0.0.1:28232"]
async_operation_limit = 0
[sync]
recover_batch_size = 100
'''


def service_unit():
    return f'''[Unit]
Description=ZEC Tip Bot empty Testnet wallet staging
Requires=zec-tip-bot-chain-testnet.service
After=zec-tip-bot-chain-testnet.service
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
Type=simple
User={USER}
Group={USER}
Environment=HOME={DATA}
WorkingDirectory={DATA}
ExecStart={BIN} --datadir {DATA} start
Restart=on-failure
RestartSec=20
TimeoutStopSec=120
UMask=0077
Nice=10
CPUQuota=200%
MemoryMax=4G
TasksMax=256
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths={DATA}
CapabilityBoundingSet=
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
'''


def marker_valid(record):
    return record == {'network': 'testnet', 'backend': 'zaino',
                      'release': '0.1.0-beta.3', 'phase': 'initialized_empty_wallet'}


def write_new(path, content, mode):
    with path.open('x') as out:
        out.write(content)
    path.chmod(mode)


def load_helper():
    path = Path('/root/check-testnet-wallet.py')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
        raise ValueError('The wallet check helper must be a root-owned file without public write permission.')
    spec = importlib.util.spec_from_file_location('wallet_check', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def status(helper):
    state = subprocess.run(['systemctl', 'is-active', UNIT], capture_output=True, text=True).stdout.strip()
    report = {'service': state, 'networkExpected': 'testnet', 'changesBotConfiguration': False}
    if state != 'active':
        return report
    step = 'node_status'
    try:
        node = helper.rpc(18232, helper.cookie_file(helper.NODE_RPC), 'getblockchaininfo')
        if not isinstance(node, dict) or node.get('chain') not in ('test', 'testnet'):
            raise ValueError('Connected node network is not Testnet.')
        step = 'wallet_authentication_cookie'
        wallet_cookie = helper.cookie_file(DATA)
        step = 'wallet_capability_read'
        discovery = helper.rpc(28232, wallet_cookie, 'rpc.discover')
        capabilities = helper.capability_report(discovery)
    except (ValueError, OSError) as exc:
        report['walletRpc'] = 'unavailable'
        report['failedCheck'] = step
        report['errorCategory'] = type(exc).__name__
        http_status = getattr(exc, 'code', None)
        if isinstance(http_status, int) and 100 <= http_status <= 599:
            report['httpStatus'] = http_status
        return report
    report['walletRpc'] = 'connected'
    report['nodeNetwork'] = node['chain']
    report['nodeHeight'] = node.get('blocks')
    report['nodeVerificationProgress'] = node.get('verificationprogress')
    report['walletCapabilities'] = [m for m in capabilities
                                    if m['method'] != 'sendrawtransaction']
    # Discovery proves connectivity even if a separate status query is still warming up.
    try:
        wallet = helper.rpc(28232, wallet_cookie, 'getwalletstatus')
        if not isinstance(wallet, dict):
            raise ValueError('Unexpected wallet status response.')
        report['walletStatusRead'] = 'available'
        report['walletSync'] = 'catching_up' if wallet.get('locked') is True else 'not_verified'
        if isinstance(wallet.get('locked'), bool):
            report['walletLockedForSync'] = wallet['locked']
        for key, output in (('node_tip', 'walletNodeHeight'), ('wallet_tip', 'walletScanHeight')):
            tip = wallet.get(key)
            height = tip.get('height') if isinstance(tip, dict) else None
            if type(height) is int and 0 <= height <= 2**32 - 1:
                report[output] = height
    except (ValueError, OSError) as exc:
        report['walletStatusRead'] = 'unavailable'
        report['walletStatusErrorCategory'] = type(exc).__name__
        report['walletSync'] = 'unknown'
    report['broadcastEndpoint'] = 'separate local Zebra RPC; not exercised by setup'
    report['currentTestnetTipVerified'] = False
    return report


def diagnose(helper):
    report = status(helper)
    credentials = []
    for directory in (helper.NODE_RPC, DATA):
        try:
            credentials.append(helper.cookie_file(directory).read_text().strip())
        except (ValueError, OSError):
            pass
    logs = subprocess.run(['journalctl', '-u', UNIT, '-n', '80', '--no-pager', '--output=cat'],
                          check=True, capture_output=True, text=True).stdout
    report['readOnly'] = True
    report['startupDiagnostics'] = helper.safe_excerpt(logs, credentials)
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--diagnose', action='store_true', help='Read status and redacted wallet service logs.')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Run this helper in the VPS root SSH terminal.')
    if Path('/etc/hostname').read_text().strip() != 'vmi3703861':
        raise ValueError('Unexpected VPS hostname.')
    os.umask(0o077)
    helper = load_helper()
    if args.diagnose:
        diagnose(helper)
        return
    if args.status:
        print(json.dumps(status(helper), indent=2))
        return
    import pwd
    node_cookie = helper.cookie_file(helper.NODE_RPC)
    node = helper.rpc(18232, node_cookie, 'getblockchaininfo')
    if not isinstance(node, dict) or node.get('chain') not in ('test', 'testnet'):
        raise ValueError('A verified Testnet node connection is required.')
    expected_config = configuration(node_cookie)
    expected_unit = service_unit()
    if UNIT_PATH.exists() and UNIT_PATH.read_text() != expected_unit:
        raise ValueError('An existing wallet service differs; refusing to overwrite it.')
    helper.binary_from_verified_archive()
    account = pwd.getpwnam(USER)
    if DATA.is_symlink():
        raise ValueError('Wallet directory must not be a symbolic link.')
    marker = DATA / '.setup.json'
    config_path = DATA / 'zallet.toml'
    identity = DATA / 'encryption-identity.txt'
    if DATA.exists():
        if (not marker.exists() or not marker_valid(json.loads(marker.read_text()))
                or config_path.read_text() != expected_config):
            raise ValueError('Existing wallet setup is incomplete or differs. It was preserved for review.')
    else:
        # Refuse an occupied RPC port before creating any new key material.
        import socket
        with socket.socket() as check_socket:
            if check_socket.connect_ex(('127.0.0.1', 28232)) == 0:
                raise ValueError('Wallet port 28232 is already occupied.')
        DATA.mkdir(mode=0o700)
        os.chown(DATA, account.pw_uid, account.pw_gid)
        write_new(config_path, expected_config, 0o600)
        os.chown(config_path, account.pw_uid, account.pw_gid)
        # An interrupted setup is deliberately held; never replace an existing identity.
        with (DATA / 'setup.log').open('x') as log:
            os.chmod(log.name, 0o600)
            for arguments in (['generate-encryption-identity', '--output', str(identity)],
                              ['init-wallet-encryption']):
                completed = subprocess.run([str(BIN), '--datadir', str(DATA)] + arguments,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=30,
                    user=account.pw_uid, group=account.pw_gid, extra_groups=[], umask=0o077,
                    cwd=DATA, env={'PATH': '/usr/bin:/bin', 'HOME': str(DATA), 'LANG': 'C.UTF-8'})
                if completed.returncode:
                    raise ValueError('Empty-wallet encryption initialization failed; existing material was preserved.')
        write_new(marker, json.dumps({'network': 'testnet', 'backend': 'zaino',
            'release': '0.1.0-beta.3', 'phase': 'initialized_empty_wallet'}) + '\n', 0o600)
    directory_metadata = DATA.stat()
    if stat.S_IMODE(directory_metadata.st_mode) != 0o700 or directory_metadata.st_uid != account.pw_uid:
        raise ValueError('Wallet data directory must be private to the wallet service user.')
    metadata = identity.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != account.pw_uid:
        raise ValueError('Wallet encryption identity must be an owner-only regular file.')
    if not UNIT_PATH.exists():
        write_new(UNIT_PATH, expected_unit, 0o644)
    subprocess.run(['systemd-analyze', 'verify', str(UNIT_PATH)], check=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', UNIT], check=True)
    print('Waiting briefly for the local wallet connection...', flush=True)
    report = None
    for _ in range(6):
        time.sleep(2)
        report = status(helper)
        if report['service'] != 'active' or report.get('walletRpc') == 'connected':
            break
    print(json.dumps(report, indent=2))
    print('Persistent empty Testnet wallet service prepared. Chain sync is still required.')
    print('No recovery phrase, spending accounts or deposits were created. Bot configuration unchanged.')
    print('Status: python3 /root/prepare-testnet-wallet.py --status')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print('Wallet setup stopped: ' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
