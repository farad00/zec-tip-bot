#!/usr/bin/env python3
"""Activate the verified Testnet indexer node, preserving its existing chain data."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import tomllib

ARTIFACTS = Path('/opt/zec-tip-bot-chain/nu7-candidate-8c5a93b-r5/artifacts')
RUNTIME = Path('/opt/zec-tip-bot-chain/nu7-runtime-8c5a93b-r5')
BIN = RUNTIME / 'zebrad'
DROPIN = Path('/etc/systemd/system/zec-tip-bot-chain-testnet.service.d/20-wallet-indexer.conf')
CONFIG = Path('/etc/zec-tip-bot-chain-testnet.toml')
DATA = Path('/var/lib/zec-tip-bot-chain-testnet')
UNIT = 'zec-tip-bot-chain-testnet.service'
WALLET_UNIT = 'zec-tip-bot-wallet-testnet.service'
USER = 'zectip-chain'
MARKER = RUNTIME / 'activation.json'


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


def require_build_metadata(record):
    expected = {'zalletRevision': '8c5a93ba9d68ad78d199c3624413c0139781b837',
                'zebraRevision': 'e8db87eb69254e0d4eedd058eadd48bdfa09065a',
                'nodeFeatures': ['default-release-binaries', 'indexer'],
                'testsPassed': True, 'closedChannelTestFix': True, 'localTestOnlyPatch': True,
                'coreSuitePassed': 418, 'cleanupChecksPassed': 50,
                'beforeClosedChannelFixSha256': '16fc83671d26fd0e8131e9230aed3ebb8b408580ca9275ab0175fd78b402b91f',
                'decryptorLibrarySourceSha256': 'e3b92c0069d6ff8af8926746e2e61308ce03c65e5eba51a650ba826744f8a81a',
                'installed': False, 'walletOpened': False, 'currentTestnetTipVerified': False}
    if any(record.get(key) != value for key, value in expected.items()):
        raise ValueError('The exact completed candidate and test evidence are required.')


def node_config(original):
    before = 'listen_addr = "127.0.0.1:18232"\n'
    if original.count(before) != 1:
        raise ValueError('Unexpected original node RPC configuration.')
    updated = original.replace(before, before + 'indexer_listen_addr = "127.0.0.1:18230"\n', 1)
    expected = tomllib.loads(original)
    expected['rpc']['indexer_listen_addr'] = '127.0.0.1:18230'
    if tomllib.loads(updated) != expected:
        raise ValueError('Node configuration changed beyond the local indexer endpoint.')
    return updated


def dropin():
    return f'''# Same pinned Zebra 7.0.0, now with its wallet-required indexer feature.
[Service]
ExecStart=
ExecStart={BIN.as_posix()} -c {CONFIG.as_posix()} start
'''


def write_new(path, content, mode):
    if path.is_symlink():
        raise ValueError('Refusing a symbolic link for installation metadata.')
    if path.exists():
        if path.read_text() != content:
            raise ValueError('An existing installation file differs; it was preserved.')
        return
    with path.open('x', encoding='utf-8', newline='\n') as file:
        file.write(content)
    path.chmod(mode)


def install_binary(source, target, expected_hash):
    def digest(path):
        with path.open('rb') as file:
            return hashlib.file_digest(file, 'sha256').hexdigest()
    if (source.is_symlink() or not source.is_file() or not re.fullmatch(r'[a-f0-9]{64}', expected_hash)
            or digest(source) != expected_hash):
        raise ValueError('Candidate binary did not match its recorded hash.')
    if target.is_symlink():
        raise ValueError('Refusing a symbolic link for a runtime executable.')
    if target.exists():
        if not target.is_file() or digest(target) != expected_hash:
            raise ValueError('Existing runtime executable differs; it was preserved.')
        return
    # Never replace a running executable or an existing different installation.
    with source.open('rb') as src, target.open('xb') as dest:
        shutil.copyfileobj(src, dest)
        dest.flush()
        os.fsync(dest.fileno())
    if digest(target) != expected_hash:
        raise ValueError('Runtime executable copy failed its hash check.')
    target.chmod(0o755)


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def candidate_running():
    result = run('systemctl', 'show', UNIT, '--property=MainPID', '--value', capture_output=True).stdout.strip()
    if not result.isdecimal() or int(result) <= 0:
        return False
    try:
        return Path('/proc').joinpath(result, 'exe').resolve() == BIN
    except OSError:
        return False


def metadata_from_node(info):
    if not isinstance(info, dict) or info.get('chain') not in ('test', 'testnet'):
        raise ValueError('Expected the local Testnet node.')
    output = {'nodeRpc': 'connected', 'nodeNetwork': info['chain'], 'currentTestnetTipVerified': False}
    height = info.get('blocks')
    if type(height) is int and 0 <= height <= 0xffff_ffff:
        output['nodeHeight'] = height
    progress = info.get('verificationprogress')
    if type(progress) in (int, float) and 0 <= progress <= 1:
        output['estimatedVerificationProgress'] = progress
    upgrades = info.get('upgrades')
    nu7 = upgrades.get('77190ad9') if isinstance(upgrades, dict) else None
    if isinstance(nu7, dict):
        output['nu7ScheduleMatches'] = nu7.get('activationheight') == 4465026
        if nu7.get('status') in ('active', 'pending', 'disabled'):
            output['nu7Status'] = nu7['status']
    else:
        output['nu7ScheduleMatches'] = None
    return output


def status(helper):
    report = {'nodeService': subprocess.run(['systemctl', 'is-active', UNIT], capture_output=True,
                                           text=True).stdout.strip(),
              'networkExpected': 'testnet', 'candidateProcess': candidate_running(),
              'walletBackendUpgradePending': True, 'spendingEnabled': False,
              'changesBotConfiguration': False, 'currentTestnetTipVerified': False}
    with socket.socket() as connection:
        connection.settimeout(1)
        report['localIndexerPortReachable'] = connection.connect_ex(('127.0.0.1', 18230)) == 0
    try:
        report.update(metadata_from_node(helper.rpc(18232, helper.cookie_file(helper.NODE_RPC), 'getblockchaininfo')))
    except (ValueError, OSError, TimeoutError) as error:
        report['nodeRpc'] = 'not ready; node may still be checking or building its local index'
        report['nodeReadErrorCategory'] = type(error).__name__
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--diagnose', action='store_true')
    args = parser.parse_args()
    base = load_helper('candidate_base', 'build-testnet-nu7-candidate.py')
    base.guard()
    helper = load_helper('wallet_check', 'check-testnet-wallet.py')
    if args.status or args.diagnose:
        report = status(helper)
        if args.diagnose:
            credentials = []
            try:
                credentials.append(helper.cookie_file(helper.NODE_RPC).read_text().strip())
            except (ValueError, OSError):
                pass
            journal = run('journalctl', '-u', UNIT, '-n', '80', '--no-pager', '--output=cat', capture_output=True).stdout
            report['nodeDiagnostics'] = helper.safe_excerpt(journal, credentials)
        print(json.dumps(report, indent=2))
        return
    chain = load_helper('chain_setup', 'prepare-testnet-chain.py')
    record = base.validate_manifest(ARTIFACTS)
    require_build_metadata(record)
    original = chain.node_config()
    updated = node_config(original)
    if CONFIG.is_symlink() or CONFIG.read_text() not in (original, updated):
        raise ValueError('Existing node configuration differs from the prepared Testnet setup.')
    if chain.UNIT_PATH.is_symlink() or chain.UNIT_PATH.read_text() != chain.service_unit():
        raise ValueError('Existing node service differs from the prepared setup.')
    if DATA.is_symlink() or not DATA.is_dir():
        raise ValueError('Existing chain directory was not found at its expected location.')
    for directory in (RUNTIME, DROPIN.parent):
        if directory.is_symlink():
            raise ValueError('Installation directory must not be a symbolic link.')
        directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    if not MARKER.exists():
        old_node = helper.rpc(18232, helper.cookie_file(helper.NODE_RPC), 'getblockchaininfo')
        metadata_from_node(old_node)
        with socket.socket() as check:
            check.settimeout(1)
            if check.connect_ex(('127.0.0.1', 18230)) == 0:
                raise ValueError('Local indexer port is occupied before activation; review the existing listener.')
    receipt = json.dumps({'schema': 1, 'network': 'testnet',
                         'zebraRevision': record['zebraRevision'], 'nodeSha256': record['binaries']['zebrad'],
                         'chainDataPreservedAt': str(DATA), 'walletUpgradePending': True}, indent=2) + '\n'
    write_new(MARKER, receipt, 0o600)
    for name in ('zebrad', 'zallet', 'zallet-zebra'):
        install_binary(ARTIFACTS / name, RUNTIME / name, record['binaries'][name])
    import pwd
    user = pwd.getpwnam(USER)
    version = run(str(BIN), '--version', capture_output=True, user=user.pw_uid, group=user.pw_gid,
                  extra_groups=[], cwd=DATA, env={'PATH': '/usr/bin:/bin', 'HOME': str(DATA), 'LANG': 'C.UTF-8'}).stdout.strip()
    if not re.fullmatch(r'zebrad 7\.0\.0\+git\.e8db87e', version):
        raise ValueError('Candidate node did not report the pinned build version.')
    write_new(RUNTIME / 'original-node.toml', original, 0o600)
    write_new(DROPIN, dropin(), 0o644)
    if candidate_running() and CONFIG.read_text() == updated:
        print(json.dumps(status(helper), indent=2))
        print('The candidate node is already running. No services were restarted or paused.')
        return
    # The old wallet cannot follow current NU7. Pause it before the node changes;
    # preserve its entire data directory and encryption identity without reading them.
    print('Pausing the old wallet backend; its data is preserved.', flush=True)
    run('systemctl', 'stop', WALLET_UNIT, timeout=150)
    run('systemctl', 'disable', WALLET_UNIT, capture_output=True)
    if CONFIG.read_text() == original:
        temporary = CONFIG.with_name(CONFIG.name + '.nu7-new')
        write_new(temporary, updated, 0o644)
        os.replace(temporary, CONFIG)
    run('systemd-analyze', 'verify', str(chain.UNIT_PATH), capture_output=True)
    run('systemctl', 'daemon-reload')
    if not candidate_running():
        print('Starting the indexer-enabled node against the existing chain data.', flush=True)
        run('systemctl', 'restart', UNIT, timeout=180)
    time.sleep(3)
    print(json.dumps(status(helper), indent=2))
    print('Node activation prepared. Initial index checking/building may still be running.')
    print('Chain data and wallet data were preserved. No accounts, funding or transfers were created.')
    print('Next: python3 /root/activate-testnet-nu7-node.py --status')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print('Activation stopped: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        print('Existing data was preserved. No automatic downgrade was attempted.', file=sys.stderr)
        sys.exit(1)
