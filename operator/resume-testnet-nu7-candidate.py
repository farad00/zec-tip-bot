#!/usr/bin/env python3
"""Resume saved compilation with upstream's bounded test-only shutdown check."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

PATCH_REV = 'b2d15011dc28a7284222f95e843880e53c4efe94'
SOURCE_CONTAINER = 'zec-nu7-candidate-8c5a93b-r2'
FAILED_CONTAINER = 'zec-nu7-candidate-8c5a93b-r3'
CONTAINER = 'zec-nu7-candidate-8c5a93b-r4'
SNAPSHOT_IMAGE = 'zec-nu7-saved:8c5a93b-r3'
IMAGE = 'zec-nu7-resume:8c5a93b-r4'
ROOT = Path('/opt/zec-tip-bot-chain/nu7-candidate-8c5a93b-r4')
PATCH_LABEL = 'org.zec-tip-bot.test-fix'

PATCHER = r'''import hashlib, json, pathlib, re, subprocess
REV = 'b2d15011dc28a7284222f95e843880e53c4efe94'
SOURCE = 'zallet-core/src/components/sync.rs'
TEST = 'wallet_sync_error_shuts_down_the_spawned_batch_decryptor'
def test_only_change(before, after):
    pattern = re.compile(r'(?ms)^    async fn ' + TEST + r'\b.*?(?=^    #\[|^    (?:async )?fn |^})')
    old = list(pattern.finditer(before))
    new = list(pattern.finditer(after))
    if len(old) != 1 or len(new) != 1:
        raise ValueError('Could not isolate the expected single test function.')
    a, b = old[0], new[0]
    prefix = before[:a.start()]
    if not re.search(r'#\[cfg\(test\)\]\s*mod tests\s*\{', prefix):
        raise ValueError('Expected function is not in the test module.')
    if prefix != after[:b.start()] or before[a.end():] != after[b.end():]:
        raise ValueError('Upstream patch changes code outside the one test; stopping.')
    changed = b.group()
    if before == after or 'timeout(' not in changed or 'from_secs(30)' not in changed:
        raise ValueError('Expected bounded shutdown assertion is missing.')
    if 'reload_keys()' not in changed or '#[ignore' in changed:
        raise ValueError('The cleanup test was not preserved.')
    return {'upstreamTestFixRevision': REV, 'testOnlyPatch': True,
            'beforeSyncSourceSha256': hashlib.sha256(before.encode()).hexdigest(),
            'afterSyncSourceSha256': hashlib.sha256(after.encode()).hexdigest()}
def git(*args, **kwargs):
    return subprocess.run(['git', *args], check=True, text=True, **kwargs)
def main():
    source = pathlib.Path(SOURCE)
    git('diff', '--exit-code', 'HEAD', capture_output=True)
    before = source.read_text()
    if before != git('show', 'HEAD:' + SOURCE, capture_output=True).stdout:
        raise ValueError('Current sync source differs from the saved source revision.')
    git('fetch', '--depth', '2', 'origin', REV)
    if git('rev-parse', 'FETCH_HEAD', capture_output=True).stdout.strip() != REV:
        raise ValueError('Unexpected fetched test-fix revision.')
    files = git('diff-tree', '--no-commit-id', '--name-only', '-r', REV,
                capture_output=True).stdout.splitlines()
    if files != [SOURCE]:
        raise ValueError('Upstream test fix changes unexpected files.')
    patch = git('show', '--format=', '--no-ext-diff', '--no-renames', REV,
                '--', SOURCE, capture_output=True).stdout
    git('apply', '--check', '-', input=patch)
    git('apply', '-', input=patch)
    after = source.read_text()
    record = test_only_change(before, after)
    pathlib.Path('/out/test-fix.json').write_text(json.dumps(record, indent=2) + '\n')
    print('Pinned upstream fix applied to the single cleanup test; runtime code unchanged.', flush=True)
if __name__ == '__main__':
    main()
'''


def load_base():
    path = Path('/root/build-testnet-nu7-candidate.py')
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != 0:
        raise ValueError('The original root-owned build helper is required.')
    spec = importlib.util.spec_from_file_location('nu7_base', path)
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    if (base.ZALLET_REV != '8c5a93ba9d68ad78d199c3624413c0139781b837'
            or base.ZEBRA_REV != 'e8db87eb69254e0d4eedd058eadd48bdfa09065a'
            or base.CONTAINER != SOURCE_CONTAINER):
        raise ValueError('Unexpected original build helper version.')
    base.guard()
    return base


def resume_script(base):
    # Continue from the saved /work directories, never redownload or reset them.
    marker = "echo 'Building Zebra 7.0.0 with the wallet-required indexer feature.'"
    if base.BUILD.count(marker) != 1:
        raise ValueError('Unexpected original build recipe.')
    tail = base.BUILD[base.BUILD.index(marker):]
    tail = tail.replace("    'testsPassed': True,", "    'testsPassed': True,\n    **json.loads((out / 'test-fix.json').read_text()),\n    **json.loads((out / 'cleanup-checks.json').read_text()),")
    return '''#!/bin/bash
set -euo pipefail
umask 0022
export GIT_TERMINAL_PROMPT=0
cd /work/zallet
test "$(git rev-parse HEAD)" = ''' + base.ZALLET_REV + '''
test "$(git -C /work/zebra rev-parse HEAD)" = ''' + base.ZEBRA_REV + '''
python3 /opt/candidate/verify.py
python3 /opt/resume/run-cleanup.py
echo 'Wallet compilation and full suites were completed in the saved builds; continuing to the node.'
''' + tail


def core_test_path(logs):
    matches = re.findall(r'Running unittests\s+src/lib\.rs\s+\((?:/work/zallet/)?(target/release/deps/zallet_core-[a-f0-9]{16})\)', logs)
    paths = set(matches)
    if len(paths) != 1:
        raise ValueError('Could not identify the exact compiled core test executable from the saved log.')
    return paths.pop()


def cleanup_runner(test_path):
    if not re.fullmatch(r'target/release/deps/zallet_core-[a-f0-9]{16}', test_path):
        raise ValueError('Unexpected compiled test executable path.')
    return '''import hashlib, json, os, pathlib, runpy, subprocess
root = pathlib.Path('/work/zallet')
source = root / 'zallet-core/src/components/sync.rs'
before = subprocess.run(['git', 'show', 'HEAD:zallet-core/src/components/sync.rs'], cwd=root,
                        check=True, text=True, capture_output=True).stdout
patcher = pathlib.Path('/opt/resume/test-fix.py')
if hashlib.sha256(patcher.read_bytes()).hexdigest() != ''' + repr(hashlib.sha256(PATCHER.encode()).hexdigest()) + ''':
    raise ValueError('Unexpected saved test-fix verifier.')
guard = runpy.run_path(str(patcher))['test_only_change']
expected = guard(before, source.read_text())
record = json.loads(pathlib.Path('/out/test-fix.json').read_text())
if expected != record:
    raise ValueError('Saved test-fix provenance disagrees with source.')
changed = subprocess.run(['git', 'diff', '--name-only', 'HEAD'], cwd=root,
                         check=True, text=True, capture_output=True).stdout.splitlines()
if changed != ['zallet-core/src/components/sync.rs']:
    raise ValueError('Unexpected source changes in the saved build.')
binary = root / ''' + repr(test_path) + '''
if binary.is_symlink() or not binary.is_file() or not binary.resolve().is_relative_to(root / 'target/release/deps'):
    raise ValueError('Unexpected compiled test file.')
with binary.open('rb') as file:
    if file.read(4) != b'\\x7fELF':
        raise ValueError('Compiled test executable is not an ELF file.')
with binary.open('rb') as file:
    test_hash = hashlib.file_digest(file, 'sha256').hexdigest()
env = {**os.environ, 'CARGO_MANIFEST_DIR': str(root / 'zallet-core'),
       'CARGO_MANIFEST_PATH': str(root / 'zallet-core/Cargo.toml')}
case = 'components::sync::tests::wallet_sync_error_shuts_down_the_spawned_batch_decryptor'
listing = subprocess.run([str(binary), '--list', '--format', 'terse'], cwd=root / 'zallet-core',
                         env=env, check=True, text=True, capture_output=True, timeout=60).stdout.splitlines()
if sum(line.endswith(': test') for line in listing) != 418 or case + ': test' not in listing:
    raise ValueError('Compiled test listing does not match the passed 418-test suite.')
print('Reusing the compiled test executable from the passed 418-test suite.', flush=True)
for attempt in range(1, 11):
    print('Cleanup check ' + str(attempt) + '/10', flush=True)
    try:
        result = subprocess.run([str(binary), case, '--exact'], cwd=root / 'zallet-core', env=env,
                                check=True, text=True, capture_output=True, timeout=90)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as failure:
        print('CLEANUP TEST DIAGNOSTIC:', flush=True)
        for captured in (failure.stdout, failure.stderr):
            if captured:
                if isinstance(captured, bytes):
                    captured = captured.decode('utf-8', errors='replace')
                print(captured[-6000:], flush=True)
        raise
    if '1 passed; 0 failed' not in result.stdout:
        raise ValueError('Cleanup check did not run exactly one passing test.')
pathlib.Path('/out/cleanup-checks.json').write_text(json.dumps({
    'cleanupChecksPassed': 10, 'coreSuitePassedBeforeResume': 418,
    'compiledCoreTestSha256': test_hash,
}, indent=2) + '\\n')
print('All 10 direct cleanup checks passed. No Cargo recompilation was needed.', flush=True)
'''


def inspect_resume(base, name=CONTAINER):
    if name not in (CONTAINER, FAILED_CONTAINER):
        raise ValueError('Unexpected resume container name.')
    result = subprocess.run(['docker', 'container', 'inspect', name], capture_output=True, text=True)
    if result.returncode:
        base.run('docker', 'info', '--format', '{{.ServerVersion}}', capture_output=True)
        return None
    records = json.loads(result.stdout)
    if len(records) != 1:
        raise ValueError('Unexpected resumed build container count.')
    record = records[0]
    labels = record['Config'].get('Labels', {})
    if (labels.get(base.LABEL) != base.ZALLET_REV or labels.get(PATCH_LABEL) != PATCH_REV
            or record.get('Mounts') or record['HostConfig'].get('Privileged')
            or record['Config'].get('User') != '10001:10001'):
        raise ValueError('Resumed build has unexpected identity or host access.')
    return record


def status(base):
    record = inspect_resume(base)
    if record is None:
        print('Resumed build has not started.')
        return
    state = record['State']
    print(json.dumps({'candidate': CONTAINER, 'state': state['Status'],
                      'exitCode': state.get('ExitCode') if state['Status'] == 'exited' else None,
                      'outOfMemory': state.get('OOMKilled', False), 'installed': False,
                      'nodeAndWalletServicesChanged': False}, indent=2))
    base.run('docker', 'logs', '--tail', '20', CONTAINER)
    if state['Status'] == 'exited' and state.get('ExitCode') == 0:
        out = ROOT / 'artifacts'
        if not out.exists():
            out.mkdir(mode=0o700)
            base.run('docker', 'cp', f'{CONTAINER}:/out/.', str(out))
        manifest = base.validate_manifest(out)
        if (manifest.get('upstreamTestFixRevision') != PATCH_REV or manifest.get('testOnlyPatch') is not True
                or manifest.get('cleanupChecksPassed') != 10 or manifest.get('coreSuitePassedBeforeResume') != 418):
            raise ValueError('Missing test-fix provenance.')
        print('BUILD VERIFIED: ' + str(out))
        print('Candidate only. Installation and current-Testnet verification are still required.')
    elif state['Status'] == 'exited':
        print('Resumed build did not pass. Existing node and wallet services were unchanged.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    base = load_base()
    if ROOT.is_symlink() or (ROOT.exists() and ROOT.resolve() != ROOT):
        raise ValueError('Unexpected resume directory.')
    if args.status or inspect_resume(base) is not None:
        status(base)
        return
    source = inspect_resume(base, FAILED_CONTAINER)
    if (source is None or source['State']['Status'] != 'exited'
            or source['State'].get('ExitCode') != 124 or source['State'].get('OOMKilled')
            or source['Config'].get('User') != '10001:10001'):
        raise ValueError('Expected the stopped r3 candidate after the repeat-check compilation timeout.')
    logs = base.run('docker', 'logs', '--tail', '1200', source['Id'], capture_output=True)
    text = logs.stdout + logs.stderr
    if ('418 passed; 0 failed; 0 ignored' not in text
            or 'Checking cleanup repeatedly; every run must pass.' not in text):
        raise ValueError('Saved build log does not confirm the full suite passed before the timeout.')
    test_path = core_test_path(text)
    if shutil.disk_usage('/opt').free < 40 * 1024 ** 3:
        raise ValueError('Fewer than 40 GiB free; review disk space before resuming.')
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    context = {'Dockerfile': f'FROM {SNAPSHOT_IMAGE}\nCOPY resume.sh run-cleanup.py /opt/resume/\nENTRYPOINT ["bash", "/opt/resume/resume.sh"]\n',
               'resume.sh': resume_script(base), 'run-cleanup.py': cleanup_runner(test_path),
               '.dockerignore': '*\n!Dockerfile\n!resume.sh\n!run-cleanup.py\n'}
    for name, content in context.items():
        target = ROOT / name
        if target.is_symlink() or (target.exists() and target.read_text() != content):
            raise ValueError('Existing resume inputs differ; refusing to overwrite them.')
        if not target.exists():
            with target.open('x', encoding='utf-8', newline='\n') as file:
                file.write(content)
            target.chmod(0o644)
    print('Saving the stopped build and its compilation cache; existing services stay running.', flush=True)
    snapshot = base.run('docker', 'commit', source['Id'], SNAPSHOT_IMAGE, capture_output=True).stdout.strip()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', snapshot):
        raise ValueError('Unexpected saved build image identity.')
    base.run('docker', 'build', '-t', IMAGE, str(ROOT))
    base.run('docker', 'run', '--detach', '--name', CONTAINER,
             '--label', base.LABEL + '=' + base.ZALLET_REV, '--label', PATCH_LABEL + '=' + PATCH_REV,
             '--cpus', '2', '--memory', '8g', '--memory-swap', '8g', '--pids-limit', '1024',
             '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
             '--log-opt', 'max-size=10m', '--log-opt', 'max-file=3', IMAGE, capture_output=True)
    print('Resumed build is running in the background. Original failed build is preserved.')
    print('Check: python3 /root/resume-testnet-nu7-candidate.py --status')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print('Stopped: ' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
