#!/usr/bin/env python3
"""Build an isolated NU7 Testnet candidate; never install it or open a wallet."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

ZALLET_REV = '8c5a93ba9d68ad78d199c3624413c0139781b837'
ZEBRA_REV = 'e8db87eb69254e0d4eedd058eadd48bdfa09065a'
BASE = 'rust:1.95.0-slim-bookworm@sha256:d7482085ff5b415f84dba5647ae71606650bdef00db7aeb69f4b3d170c3e4082'
ROOT = Path('/opt/zec-tip-bot-chain/nu7-candidate-8c5a93b-r2')
CONTAINER = 'zec-nu7-candidate-8c5a93b-r2'
IMAGE = 'zec-nu7-builder:8c5a93b-r2'
LABEL = 'org.zec-tip-bot.candidate'

# Only these generated public build inputs enter the Docker context. No app,
# chain, wallet, SSH files, Docker socket, ports or host volumes are passed in.
DOCKERFILE = f'''FROM {BASE}
RUN apt-get update && apt-get install -y --no-install-recommends \\
    build-essential clang llvm-dev libclang-dev protobuf-compiler pkg-config \\
    git ca-certificates python3 libssl-dev cmake \\
    && apt-get clean
RUN useradd --uid 10001 --create-home --shell /bin/bash builder \\
    && install -d -o builder -g builder /work /out
COPY build.sh verify.py /opt/candidate/
ENV PROTOC=/usr/bin/protoc CARGO_BUILD_JOBS=2 CARGO_INCREMENTAL=0 \\
    CARGO_PROFILE_RELEASE_DEBUG=0 HOME=/home/builder
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["bash", "/opt/candidate/build.sh"]
'''

VERIFY = '''import hashlib, json, os, pathlib, subprocess, tomllib
ROOT = pathlib.Path('/work')
def lock_packages(relative):
    data = tomllib.loads((ROOT / relative).read_text())
    return data['package']
def require(packages, name, version):
    found = [p for p in packages if p['name'] == name]
    if len(found) != 1 or found[0]['version'] != version:
        raise ValueError('Unexpected locked dependency: ' + name)
    if found[0].get('source') != 'registry+https://github.com/rust-lang/crates.io-index':
        raise ValueError('Unexpected dependency source: ' + name)
    if len(found[0].get('checksum', '')) != 64:
        raise ValueError('Missing registry checksum: ' + name)
def workspace_package(name):
    if name not in ('zebra-state', 'zebra-rpc', 'zebra-chain'):
        raise ValueError('Unexpected workspace package name')
    manifest = ROOT / 'zebra' / name / 'Cargo.toml'
    if manifest.is_symlink() or manifest.resolve().parent.parent != (ROOT / 'zebra').resolve():
        raise ValueError('Unexpected workspace manifest location')
    return tomllib.loads(manifest.read_text())['package']
def require_workspace(packages, name, version):
    found = [p for p in packages if p['name'] == name]
    if (len(found) != 1 or found[0]['version'] != version
            or 'source' in found[0] or 'checksum' in found[0]):
        raise ValueError('Unexpected locked workspace dependency: ' + name)
    package = workspace_package(name)
    if package.get('name') != name or package.get('version') != version:
        raise ValueError('Pinned source manifest disagrees with workspace lock: ' + name)
def verify_dependencies():
    wallet = lock_packages('zallet/Cargo.lock')
    backend = lock_packages('zallet/backends/zebra/Cargo.lock')
    node = lock_packages('zebra/Cargo.lock')
    for packages in (wallet, backend, node):
        require(packages, 'zcash_protocol', '0.11.0')
    for packages in (wallet, backend):
        require(packages, 'zcash_client_backend', '0.25.0')
        require(packages, 'zcash_client_sqlite', '0.23.0')
    for name, version in (('zebra-state', '16.0.0'), ('zebra-rpc', '19.0.0'), ('zebra-chain', '15.0.0')):
        # Zallet resolves published Zebra crates; Zebra resolves its own source
        # workspace. Both must match, but workspace entries have no registry hash.
        require(backend, name, version)
        require_workspace(node, name, version)
if __name__ == '__main__':
    verify_dependencies()
    print('Locked released NU7 dependency cohort verified.', flush=True)
'''

BUILD = f'''#!/bin/bash
set -euo pipefail
umask 0022
export GIT_TERMINAL_PROMPT=0
fetch_commit() {{
    git init "$1"
    git -C "$1" remote add origin "$2"
    git -C "$1" fetch --depth 1 origin "$3"
    git -C "$1" checkout --detach FETCH_HEAD
    test "$(git -C "$1" rev-parse HEAD)" = "$3"
}}
fetch_commit /work/zallet https://github.com/zcash/zallet.git {ZALLET_REV}
fetch_commit /work/zebra https://github.com/ZcashFoundation/zebra.git {ZEBRA_REV}
python3 /opt/candidate/verify.py
echo 'Building candidate wallet; no wallet data is accessible to this container.'
cd /work/zallet
cargo +1.95.0 build --release --locked --jobs 2 --manifest-path backends/zebra/Cargo.toml --bin zallet-zebra --features rpc-cli
cargo +1.95.0 test --release --locked --jobs 2 --manifest-path backends/zebra/Cargo.toml --bin zallet-zebra --features rpc-cli
cargo +1.95.0 build --release --locked --jobs 2 --bin zallet
cargo +1.95.0 test --release --locked --jobs 2 -p zallet-core --lib
echo 'Building Zebra 7.0.0 with the wallet-required indexer feature.'
cd /work/zebra
cargo +1.95.0 build --release --locked --jobs 2 -p zebrad --bin zebrad --features 'default-release-binaries indexer'
python3 /opt/candidate/verify.py
install -m 0755 /work/zallet/target/release/zallet /out/zallet
install -m 0755 /work/zallet/backends/zebra/target/release/zallet-zebra /out/zallet-zebra
install -m 0755 /work/zebra/target/release/zebrad /out/zebrad
/out/zallet --version
/out/zallet-zebra --version
/out/zebrad --version
python3 - <<'PY'
import hashlib, json
from pathlib import Path
out = Path('/out')
manifest = {{
    'schema': 1,
    'zalletRevision': '{ZALLET_REV}',
    'zebraRevision': '{ZEBRA_REV}',
    'upstreamPullRequest': 'https://github.com/zcash/zallet/pull/898',
    'unmergedExperimentalSource': True,
    'nodeFeatures': ['default-release-binaries', 'indexer'],
    'testsPassed': True,
    'installed': False,
    'currentTestnetTipVerified': False,
    'walletOpened': False,
    'binaries': {{name: hashlib.file_digest((out / name).open('rb'), 'sha256').hexdigest()
                 for name in ('zallet', 'zallet-zebra', 'zebrad')}},
}}
(out / 'candidate.json').write_text(json.dumps(manifest, indent=2) + '\\n')
PY
echo 'CANDIDATE BUILD COMPLETE. Not installed; no wallet or transfers were created.'
'''


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def guard():
    if platform.system() != 'Linux' or os.geteuid() != 0:
        raise ValueError('Run this helper in the existing VPS root SSH terminal.')
    if platform.machine() != 'x86_64' or Path('/etc/hostname').read_text().strip() != 'vmi3703861':
        raise ValueError('Unexpected host; no build started.')
    release = Path('/etc/os-release').read_text()
    if not re.search(r'^ID=ubuntu$', release, re.M) or 'VERSION_ID="24.04"' not in release:
        raise ValueError('This candidate is prepared for Ubuntu 24.04.')
    if ROOT.is_symlink() or (ROOT.exists() and ROOT.resolve() != ROOT):
        raise ValueError('Unexpected candidate directory.')


def inputs():
    return {'Dockerfile': DOCKERFILE, 'build.sh': BUILD, 'verify.py': VERIFY,
            '.dockerignore': '*\n!Dockerfile\n!build.sh\n!verify.py\n'}


def inspect():
    result = subprocess.run(['docker', 'container', 'inspect', CONTAINER],
                            capture_output=True, text=True)
    if result.returncode:
        # Distinguish an absent container from a broken Docker daemon.
        run('docker', 'info', '--format', '{{.ServerVersion}}', capture_output=True)
        return None
    records = json.loads(result.stdout)
    if len(records) != 1 or records[0]['Config'].get('Labels', {}).get(LABEL) != ZALLET_REV:
        raise ValueError('Existing container does not belong to this candidate.')
    if records[0].get('Mounts') or records[0]['HostConfig'].get('Privileged'):
        raise ValueError('Candidate container has unexpected host access.')
    return records[0]


def validate_manifest(folder):
    file = folder / 'candidate.json'
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 4096:
        raise ValueError('Candidate manifest is absent or invalid.')
    manifest = json.loads(file.read_text())
    expected = {'schema': 1, 'zalletRevision': ZALLET_REV, 'zebraRevision': ZEBRA_REV,
                'upstreamPullRequest': 'https://github.com/zcash/zallet/pull/898',
                'unmergedExperimentalSource': True,
                'nodeFeatures': ['default-release-binaries', 'indexer'],
                'testsPassed': True, 'installed': False,
                'currentTestnetTipVerified': False, 'walletOpened': False}
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError('Unexpected candidate build metadata.')
    hashes = manifest.get('binaries', {})
    if set(hashes) != {'zallet', 'zallet-zebra', 'zebrad'}:
        raise ValueError('Unexpected candidate binary list.')
    for name, expected_hash in hashes.items():
        target = folder / name
        if (not re.fullmatch(r'[a-f0-9]{64}', str(expected_hash)) or target.is_symlink()
                or not target.is_file() or not 1_000_000 <= target.stat().st_size <= 800_000_000):
            raise ValueError('Invalid candidate executable.')
        with target.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != expected_hash:
                raise ValueError('Candidate executable checksum mismatch.')
    return manifest


def status():
    record = inspect()
    if record is None:
        print('Candidate build has not started.')
        return
    state = record['State']
    print(json.dumps({'candidate': CONTAINER, 'state': state['Status'],
                      'exitCode': state.get('ExitCode') if state['Status'] == 'exited' else None,
                      'outOfMemory': state.get('OOMKilled', False),
                      'installed': False, 'nodeAndWalletServicesChanged': False}, indent=2))
    # This container has no host data, secrets or wallet configuration.
    run('docker', 'logs', '--tail', '15', CONTAINER)
    if state['Status'] == 'exited' and state.get('ExitCode') == 0:
        out = ROOT / 'artifacts'
        if not out.exists():
            out.mkdir(mode=0o700)
            run('docker', 'cp', f'{CONTAINER}:/out/.', str(out))
        validate_manifest(out)
        print('BUILD VERIFIED: ' + str(out))
        print('Candidate only. Installation and current-Testnet checks are still required.')
    elif state['Status'] == 'exited':
        print('Build did not pass. Existing services and wallet data were untouched.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true', help='Read isolated build status and collect completed output.')
    args = parser.parse_args()
    guard()
    if args.status or inspect() is not None:
        status()
        return
    if shutil.disk_usage('/opt').free < 40 * 1024 ** 3:
        raise ValueError('Fewer than 40 GiB free; do not start the candidate build.')
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, content in inputs().items():
        path = ROOT / name
        if path.is_symlink() or (path.exists() and path.read_text() != content):
            raise ValueError('Existing build inputs differ; refusing to overwrite them.')
        if not path.exists():
            with path.open('x', encoding='utf-8', newline='\n') as dest:
                dest.write(content)
            path.chmod(0o644)
    print('Preparing an isolated build image. No existing service will be changed.', flush=True)
    run('docker', 'build', '--pull', '-t', IMAGE, str(ROOT))
    run('docker', 'run', '--detach', '--name', CONTAINER,
        '--label', LABEL + '=' + ZALLET_REV,
        '--cpus', '2', '--memory', '8g', '--memory-swap', '8g', '--pids-limit', '1024',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
        '--log-opt', 'max-size=10m', '--log-opt', 'max-file=3', IMAGE,
        capture_output=True)
    print('Candidate compilation is running in the background; SSH can close safely.')
    print('No installation, service restart, key generation or transfer was performed.')
    print('Check: python3 /root/build-testnet-nu7-candidate.py --status')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print('Stopped: ' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
