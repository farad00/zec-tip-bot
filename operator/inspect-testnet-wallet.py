#!/usr/bin/env python3
"""Read wallet status with a measured timeout and optionally export PUBLIC build sources."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

WALLET = Path('/var/lib/zec-tip-bot-wallet-nu7-testnet')
IMAGE = 'zec-nu7-finish:8c5a93b-r5'
SOURCES = Path('/root/zec-wallet-api-sources.json')
REVISION = '8c5a93ba9d68ad78d199c3624413c0139781b837'

COLLECT = r'''import json, pathlib, subprocess, sys
root = pathlib.Path('/work/zallet')
revision = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                          check=True, text=True, capture_output=True).stdout.strip()
if revision != '8c5a93ba9d68ad78d199c3624413c0139781b837':
    raise ValueError('Unexpected public source revision')
files = {}
fixed = ['zallet-core/src/cli.rs', 'zallet-core/src/config.rs',
         'zallet-core/src/components/database.rs', 'zallet-core/src/components/keystore.rs',
         'zallet-core/src/components/json_rpc/methods.rs',
         'zallet-core/src/components/json_rpc/server.rs', 'backends/zebra/src/chain.rs']
folders = ['zallet-core/src/commands', 'zallet-core/src/components/database',
           'zallet-core/src/components/keystore', 'backends/zebra/src/chain']
paths = [root / name for name in fixed]
for folder in folders:
    paths.extend((root / folder).rglob('*.rs'))
rpc = root / 'zallet-core/src/components/json_rpc/methods'
for pattern in ('pczt*.rs', '*account*.rs', '*address_for_account*.rs', '*wallet_status*.rs'):
    paths.extend(rpc.glob(pattern))
for path in sorted(set(paths)):
    if not path.exists():
        continue
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError('Unexpected public source path')
    if path.stat().st_size > 1000000:
        raise ValueError('Unexpected public source size')
    files[str(path.relative_to(root))] = path.read_text()
if sum(len(value) for value in files.values()) > 4000000:
    raise ValueError('Public source bundle too large')
json.dump({'revision': revision, 'files': files}, sys.stdout)
'''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def select_status(value):
    if not isinstance(value, dict) or not isinstance(value.get('locked'), bool):
        raise ValueError('Unexpected wallet status schema.')
    result = {'walletStatusRead': 'available', 'walletLockedForSync': value['locked']}
    for field, output in (('node_tip', 'walletNodeHeight'), ('wallet_tip', 'walletScanHeight')):
        tip = value.get(field)
        height = tip.get('height') if isinstance(tip, dict) else None
        if type(height) is int and 0 <= height <= 0xffff_ffff:
            result[output] = height
    synced = value.get('fully_synced_height')
    if type(synced) is int and 0 <= synced <= 0xffff_ffff:
        result['fullySyncedHeight'] = synced
    work = value.get('sync_work_remaining')
    count = work.get('unscanned_blocks') if isinstance(work, dict) else None
    if type(count) is int and 0 <= count <= 0xffff_ffff:
        result['unscannedBlocks'] = count
    return result


def read_status(check):
    credential = check.cookie_file(WALLET).read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_]+:[!-~]{1,4096}', credential):
        raise ValueError('Unexpected local wallet authentication format.')
    request = urllib.request.Request('http://127.0.0.1:28232/',
        data=json.dumps({'jsonrpc': '2.0', 'id': 'measured-status',
                         'method': 'getwalletstatus', 'params': []}).encode(),
        headers={'Content-Type': 'application/json',
                 'Authorization': 'Basic ' + base64.b64encode(credential.encode()).decode()})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    started = time.monotonic()
    result = {'walletStatusRead': 'unavailable', 'clientTimeoutSeconds': 25}
    try:
        with opener.open(request, timeout=25) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('Unexpected wallet status response size.')
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get('id') != 'measured-status':
            raise ValueError('Unexpected wallet status response identifier.')
        if payload.get('error'):
            error = payload['error']
            result['statusErrorCategory'] = 'rpc_rejected'
            if isinstance(error, dict) and type(error.get('code')) is int:
                result['rpcErrorCode'] = error['code']
        else:
            result.update(select_status(payload.get('result')))
    except (OSError, ValueError, TimeoutError) as error:
        result['statusErrorCategory'] = type(error).__name__
        code = getattr(error, 'code', None)
        if type(code) is int and 100 <= code <= 599:
            result['httpStatus'] = code
    result['elapsedSeconds'] = round(time.monotonic() - started, 2)
    return result


def collect_sources():
    # There are no host mounts, ports or credentials in this temporary container.
    result = subprocess.run(['docker', 'run', '--rm', '-i', '--network', 'none',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
        '--memory', '256m', '--pids-limit', '64', '--entrypoint', 'python3', IMAGE, '-'],
        input=COLLECT, check=True, text=True, capture_output=True, timeout=45)
    if len(result.stdout) > 5_000_000:
        raise ValueError('Unexpected public source output size.')
    document = json.loads(result.stdout)
    if document.get('revision') != REVISION or not isinstance(document.get('files'), dict):
        raise ValueError('Unexpected public source bundle metadata.')
    if not document['files'] or any(not isinstance(value, str) for value in document['files'].values()):
        raise ValueError('Unexpected public source content.')
    content = json.dumps(document).encode()
    if SOURCES.is_symlink():
        raise ValueError('Public source destination must not be a symbolic link.')
    if SOURCES.exists():
        if SOURCES.read_bytes() != content:
            raise ValueError('An existing public source bundle differs; it was preserved.')
    else:
        with SOURCES.open('xb') as file:
            file.write(content)
        SOURCES.chmod(0o600)
    return {'publicSourceFile': str(SOURCES), 'sourceFiles': len(document['files']),
            'publicSourceSha256': hashlib.sha256(content).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect-sources', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Run this helper in the existing VPS root terminal.')
    import importlib.util
    helper_path = Path('/root/activate-testnet-nu7-wallet.py')
    metadata = helper_path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022):
        raise ValueError('Expected the root-owned wallet activation helper.')
    spec = importlib.util.spec_from_file_location('wallet_activation', helper_path)
    activation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(activation)
    base = activation.load_helper('build_base', 'build-testnet-nu7-candidate.py')
    base.guard()
    check = activation.load_helper('wallet_check', 'check-testnet-wallet.py')
    report = {'readOnlyWalletOperations': True, 'changesServices': False, 'createsKeysOrAccounts': False,
              'currentTestnetTipVerified': False}
    report.update(read_status(check))
    if args.collect_sources:
        report.update(collect_sources())
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print('Inspection stopped: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        sys.exit(1)
