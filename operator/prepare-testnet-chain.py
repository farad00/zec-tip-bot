#!/usr/bin/env python3
"""Install only a pinned Zebra Testnet node. No wallet keys or X credentials."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

RELEASE = 'v7.0.0'
REPO = 'ZcashFoundation/zebra'
ROOT = Path('/opt/zec-tip-bot-chain')
DATA = Path('/var/lib/zec-tip-bot-chain-testnet')
CONFIG = Path('/etc/zec-tip-bot-chain-testnet.toml')
UNIT_PATH = Path('/etc/systemd/system/zec-tip-bot-chain-testnet.service')
UNIT = UNIT_PATH.name
BIN = ROOT / RELEASE / 'zebrad'
USER = 'zectip-chain'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def select_asset(release):
    if release.get('tag_name') != RELEASE or release.get('draft') or release.get('prerelease'):
        raise ValueError('The expected stable Zebra release was not returned.')
    candidates = []
    for asset in release.get('assets', []):
        name = asset.get('name', '')
        # Only an archive of the Linux x86-64 node, never a source tree or sidecar.
        if (re.fullmatch(r'[A-Za-z0-9._+-]+', name) and name.startswith('zebrad')
                and 'linux' in name.lower() and re.search(r'(amd64|x86_64)', name)
                and name.endswith('.tar.gz') and not re.search(r'(compat|source|debug)', name)):
            candidates.append(asset)
    if len(candidates) != 1:
        names = ', '.join(str(a.get('name', '')) for a in release.get('assets', []))
        raise ValueError('No unique Linux amd64 node archive. Published assets: ' + names)
    asset = candidates[0]
    expected_prefix = f'https://github.com/{REPO}/releases/download/{RELEASE}/'
    if asset.get('browser_download_url') != expected_prefix + asset['name']:
        raise ValueError('The release download URL did not match the official repository.')
    if not isinstance(asset.get('digest'), str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', asset['digest']):
        raise ValueError('The official release did not supply a SHA256 digest; stopping.')
    if not 1_000_000 <= asset.get('size', 0) <= 500_000_000:
        raise ValueError('Unexpected node archive size.')
    return asset


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={
        'User-Agent': 'zec-tip-bot-testnet-setup',
        'Accept': 'application/vnd.github+json' if url.startswith('https://api.github.com/') else '*/*',
    }), timeout=90)


def download_verified(asset, target):
    digest = hashlib.sha256()
    count = 0
    with request(asset['browser_download_url']) as source, target.open('xb') as dest:
        while chunk := source.read(1024 * 1024):
            count += len(chunk)
            if count > asset['size']:
                raise ValueError('Downloaded archive exceeds the published size.')
            digest.update(chunk)
            dest.write(chunk)
    if count != asset['size'] or digest.hexdigest() != asset['digest'][7:]:
        raise ValueError('Node archive verification failed. Nothing will be installed.')


def extract_binary(archive, directory):
    # Never extract a complete remote archive as root: select one regular binary.
    with tarfile.open(archive, 'r:gz') as tar:
        matches = []
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name:
                raise ValueError('Unsafe archive path.')
            if path.name == 'zebrad':
                if not member.isfile() or not 1_000_000 <= member.size <= 600_000_000:
                    raise ValueError('The node must be a regular executable file.')
                matches.append(member)
        if len(matches) != 1:
            raise ValueError('Archive did not contain exactly one node executable.')
        target = directory / 'zebrad'
        source = tar.extractfile(matches[0])
        if source is None:
            raise ValueError('Could not read node executable.')
        with source, target.open('xb') as dest:
            shutil.copyfileobj(source, dest)
        target.chmod(0o755)
        return target


def node_config():
    return '''# Testnet ONLY. No wallet keys. RPC is local and cookie-authenticated.
[network]
network = "Testnet"
listen_addr = "127.0.0.1:18233"

[state]
cache_dir = "/var/lib/zec-tip-bot-chain-testnet/cache"

[rpc]
listen_addr = "127.0.0.1:18232"
cookie_dir = "/var/lib/zec-tip-bot-chain-testnet/rpc"
enable_cookie_auth = true
'''


def service_unit():
    return f'''[Unit]
Description=ZEC Tip Bot Zebra Testnet node (no wallet)
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User={USER}
Group={USER}
Environment=HOME={DATA}
WorkingDirectory={DATA}
ExecStart={BIN} -c {CONFIG} start
Restart=on-failure
RestartSec=15
TimeoutStopSec=120
UMask=0077
Nice=10
CPUQuota=400%
MemoryMax=16G
TasksMax=512
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


def write_new(path, content, mode=0o644):
    if path.exists():
        if path.read_text() != content:
            raise ValueError(f'Existing setup differs; refusing to overwrite {path}.')
        return
    with path.open('x') as out:
        out.write(content)
    path.chmod(mode)


def node_status():
    state = subprocess.run(['systemctl', 'is-active', UNIT], capture_output=True, text=True).stdout.strip()
    result = {'service': state, 'expectedNetwork': 'testnet', 'walletCreated': False}
    if state != 'active':
        return result
    cookies = [p for p in (DATA / 'rpc').glob('*') if p.is_file() and 'cookie' in p.name.lower()]
    if len(cookies) != 1:
        result['nodeRpc'] = 'waiting for local authentication cookie'
        return result
    credential = cookies[0].read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_]+:[!-~]{1,4096}', credential):
        raise ValueError('Unexpected local cookie format; credentials were not printed.')
    body = json.dumps({'jsonrpc': '2.0', 'id': 'chain-status', 'method': 'getblockchaininfo', 'params': []}).encode()
    req = urllib.request.Request('http://127.0.0.1:18232', data=body, headers={
        'Content-Type': 'application/json',
        'Authorization': 'Basic ' + base64.b64encode(credential.encode()).decode(),
    })
    # Ignore proxy environment variables for this local authenticated request.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as response:
            payload = json.loads(response.read(1024 * 1024))
    except (urllib.error.URLError, TimeoutError):
        result['nodeRpc'] = 'starting; run the status command again shortly'
        return result
    if payload.get('id') != 'chain-status' or payload.get('error') or not isinstance(payload.get('result'), dict):
        result['nodeRpc'] = 'waiting for chain initialization'
        return result
    info = payload['result']
    if info.get('chain') not in ('test', 'testnet'):
        raise ValueError('Unexpected node network. Stop the node and review its configuration.')
    result.update({'nodeRpc': 'connected', 'network': info['chain'], 'height': info.get('blocks'),
                   'verificationProgress': info.get('verificationprogress')})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true', help='Read node status only.')
    args = parser.parse_args()
    if platform.system() != 'Linux' or os.geteuid() != 0:
        raise ValueError('Run this helper in the VPS root SSH terminal.')
    if args.status:
        print(json.dumps(node_status(), indent=2))
        return
    if platform.machine() != 'x86_64':
        raise ValueError('This setup expects the verified x86_64 VPS.')
    os_release = Path('/etc/os-release').read_text()
    if not re.search(r'^ID=ubuntu$', os_release, re.M) or 'VERSION_ID="24.04"' not in os_release:
        raise ValueError('This setup is prepared for Ubuntu 24.04.')
    if Path('/etc/hostname').read_text().strip() != 'vmi3703861':
        raise ValueError('Unexpected host. No installation performed.')
    for target, content in ((CONFIG, node_config()), (UNIT_PATH, service_unit())):
        if target.exists() and target.read_text() != content:
            raise ValueError(f'Existing file differs; refusing to overwrite {target}.')
    if BIN.exists():
        record_file = ROOT / RELEASE / 'release.json'
        record = json.loads(record_file.read_text())
        if record.get('tag') != RELEASE or hashlib.sha256(BIN.read_bytes()).hexdigest() != record.get('binarySha256'):
            raise ValueError('Existing binary does not match its recorded installation.')
    else:
        print('Reading the official Zebra v7.0.0 release metadata...', flush=True)
        with request(f'https://api.github.com/repos/{REPO}/releases/tags/{RELEASE}') as response:
            release = json.load(response)
        asset = select_asset(release)
        print('Downloading and checking ' + asset['name'] + '...', flush=True)
        with tempfile.TemporaryDirectory(prefix='zec-chain-') as tmp:
            directory = Path(tmp)
            archive = directory / 'node.tar.gz'
            download_verified(asset, archive)
            binary = extract_binary(archive, directory)
            version = run(str(binary), '--version', capture_output=True).stdout.strip()
            if not re.search(r'\b7\.0\.0(?:\+|\s|$)', version):
                raise ValueError('Downloaded binary did not report Zebra 7.0.0.')
            print(version, flush=True)
            help_output = run(str(binary), '--help', capture_output=True).stdout
            if '--config' not in help_output or 'start' not in help_output:
                raise ValueError('Unexpected node command interface.')
            BIN.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(binary, BIN)
            BIN.chmod(0o755)
            write_new(BIN.parent / 'release.json', json.dumps({
                'tag': RELEASE, 'asset': asset['name'], 'archiveDigest': asset['digest'],
                'binarySha256': hashlib.sha256(BIN.read_bytes()).hexdigest(),
            }, indent=2) + '\n')
    if subprocess.run(['getent', 'passwd', USER], stdout=subprocess.DEVNULL).returncode:
        run('useradd', '--system', '--user-group', '--home-dir', str(DATA), '--no-create-home',
            '--shell', '/usr/sbin/nologin', USER)
    run('install', '-d', '-m', '0700', '-o', USER, '-g', USER, str(DATA), str(DATA / 'rpc'))
    write_new(CONFIG, node_config())
    write_new(UNIT_PATH, service_unit())
    run('systemd-analyze', 'verify', str(UNIT_PATH))
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', UNIT)
    time.sleep(3)
    status = node_status()
    print(json.dumps(status, indent=2))
    if status['service'] != 'active':
        print('Node is not active. Read only its node log with:')
        print(f'journalctl -u {UNIT} -n 25 --no-pager')
        sys.exit(1)
    print('Node service started; initial chain synchronization is not finished.')
    print('No wallet, keys, deposits or transfers were created.')
    print('Status: python3 /root/prepare-testnet-chain.py --status')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        # Never echo HTTP response bodies, cookies, environment or subprocess stderr.
        if isinstance(exc, ValueError):
            print('Stopped: ' + str(exc), file=sys.stderr)
        else:
            print('Setup stopped (' + type(exc).__name__ + '). No wallet secrets were printed.', file=sys.stderr)
        sys.exit(1)
