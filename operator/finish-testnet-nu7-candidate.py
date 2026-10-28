#!/usr/bin/env python3
"""Finish the isolated Testnet candidate using a channel-closure cleanup test."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

SOURCE_CONTAINER = 'zec-nu7-candidate-8c5a93b-r4'
CONTAINER = 'zec-nu7-candidate-8c5a93b-r5'
SNAPSHOT = 'zec-nu7-saved:8c5a93b-r4'
IMAGE = 'zec-nu7-finish:8c5a93b-r5'
ROOT = Path('/opt/zec-tip-bot-chain/nu7-candidate-8c5a93b-r5')
FIX_LABEL = 'org.zec-tip-bot.cleanup-observer'
SOURCE_HASH = '16fc83671d26fd0e8131e9230aed3ebb8b408580ca9275ab0175fd78b402b91f'
LIBRARY_HASH = 'e3b92c0069d6ff8af8926746e2e61308ce03c65e5eba51a650ba826744f8a81a'

PATCHER = r'''import hashlib, json, os, pathlib, re, subprocess
SOURCE_HASH = '16fc83671d26fd0e8131e9230aed3ebb8b408580ca9275ab0175fd78b402b91f'
LIBRARY_HASH = 'e3b92c0069d6ff8af8926746e2e61308ce03c65e5eba51a650ba826744f8a81a'
CASE = 'wallet_sync_error_shuts_down_the_spawned_batch_decryptor'
CONTROL = """        // A live receiver must not satisfy the shutdown signal.
        let (live_observer, live_engine) = WalletSync::build_decryptor();
        assert!(!live_observer.is_closed());
        assert!(
            tokio::time::timeout(Duration::from_millis(25), live_observer.closed())
                .await
                .is_err(),
            "shutdown observation must remain pending while the engine is alive",
        );
        drop(live_engine);
        tokio::time::timeout(Duration::from_secs(2), live_observer.closed())
            .await
            .expect("dropping the live engine closes its channel");
        assert!(live_observer.is_closed());

"""
OBSERVATION = """        // Queued requests can race receiver shutdown and leave their one-shot
        // replies stranded. Observe the receiver's closure directly instead.
        tokio::time::timeout(Duration::from_secs(30), decryptor_observer.closed())
            .await
            .expect("failed wallet sync startup must close its batch decryptor channel");
        assert!(decryptor_observer.is_closed());
    }
"""
def without_test(source):
    pattern = re.compile(r'(?ms)^    async fn ' + CASE + r'\b.*?^    }\n')
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        raise ValueError('Expected exactly one cleanup test.')
    match = matches[0]
    prefix = source[:match.start()]
    if not re.search(r'#\[cfg\(test\)\]\s*mod tests\s*\{', prefix):
        raise ValueError('Expected a test-only function.')
    return prefix, match.group(), source[match.end():]
def patch_source(source):
    if hashlib.sha256(source.encode()).hexdigest() != SOURCE_HASH:
        raise ValueError('Saved sync source differs from the inspected source bundle.')
    prefix, test, suffix = without_test(source)
    setup = '        crate::i18n::load_languages(&[]);\n\n'
    comment = '        // The failed startup aborts the batch decryptor task, but the abort only lands\n'
    if test.count(setup) != 1 or test.count(comment) != 1:
        raise ValueError('Unexpected cleanup test structure.')
    test = test[:test.index(comment)] + OBSERVATION
    test = test.replace(setup, setup + CONTROL, 1)
    after = prefix + test + suffix
    if without_test(after)[::2] != (prefix, suffix):
        raise ValueError('A change escaped the test-only function.')
    return after
def main():
    root = pathlib.Path('/work/zallet')
    source = root / 'zallet-core/src/components/sync.rs'
    before = source.read_text()
    original = subprocess.run(['git', 'show', 'HEAD:zallet-core/src/components/sync.rs'],
                              cwd=root, check=True, text=True, capture_output=True).stdout
    if without_test(before)[::2] != without_test(original)[::2]:
        raise ValueError('Saved source differs from the pinned revision outside the test.')
    changed = subprocess.run(['git', 'diff', '--name-only', 'HEAD'], cwd=root,
                             check=True, text=True, capture_output=True).stdout.splitlines()
    if changed != ['zallet-core/src/components/sync.rs']:
        raise ValueError('Unexpected saved wallet source changes.')
    crates = set()
    for cargo in (pathlib.Path(os.environ.get('CARGO_HOME', '/usr/local/cargo')),
                  pathlib.Path('/home/builder/.cargo')):
        crates.update((cargo / 'registry/src').glob('*/zcash_client_backend-0.25.0'))
    if len(crates) != 1:
        raise ValueError('Expected exactly one inspected backend library.')
    library = crates.pop() / 'src/sync/decryptor.rs'
    if library.is_symlink() or hashlib.sha256(library.read_bytes()).hexdigest() != LIBRARY_HASH:
        raise ValueError('Backend library differs from the inspected source bundle.')
    after = patch_source(before)
    source.write_text(after)
    pathlib.Path('/out/closed-channel-test-fix.json').write_text(json.dumps({
        'closedChannelTestFix': True, 'localTestOnlyPatch': True,
        'beforeClosedChannelFixSha256': SOURCE_HASH,
        'afterClosedChannelFixSha256': hashlib.sha256(after.encode()).hexdigest(),
        'decryptorLibrarySourceSha256': LIBRARY_HASH,
    }, indent=2) + '\n')
    print('Cleanup test now observes channel closure, with a live-engine negative control.', flush=True)
    print('Wallet runtime code and dependency versions are unchanged.', flush=True)
if __name__ == '__main__':
    main()
'''

CHECKS = r'''import hashlib, json, os, pathlib, re, subprocess
ROOT = pathlib.Path('/work/zallet')
CASE = 'components::sync::tests::wallet_sync_error_shuts_down_the_spawned_batch_decryptor'
def select_artifact(messages):
    paths = set()
    for message in messages:
        if (message.get('reason') == 'compiler-artifact'
                and message.get('target', {}).get('name', '').replace('-', '_') == 'zallet_core'
                and message.get('profile', {}).get('test') is True
                and isinstance(message.get('executable'), str)):
            paths.add(message['executable'])
    if len(paths) != 1:
        raise ValueError('Expected exactly one freshly compiled core test executable.')
    return paths.pop()
def require_summary(text, count):
    if count not in (1, 418):
        raise ValueError('Unexpected expected test count.')
    filtered = 418 - count
    if not re.search(r'test result: ok\. ' + str(count) + r' passed; 0 failed; 0 ignored; 0 measured; ' + str(filtered) + r' filtered out;', text):
        raise ValueError('Test summary did not show the exact expected passing test count.')
def checked(command, count, timeout, env):
    try:
        result = subprocess.run(command, cwd=ROOT / 'zallet-core', env=env,
                                check=True, text=True, capture_output=True, timeout=timeout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print('TEST FAILURE DETAILS:', flush=True)
        for output in (error.stdout, error.stderr):
            if output:
                if isinstance(output, bytes):
                    output = output.decode('utf-8', errors='replace')
                print(output[-10000:], flush=True)
        raise
    require_summary(result.stdout, count)
def main():
    messages = [json.loads(line) for line in pathlib.Path('/out/core-artifacts.jsonl').read_text().splitlines() if line.strip()]
    binary = pathlib.Path(select_artifact(messages))
    expected = ROOT / 'target/release/deps'
    if (binary.is_symlink() or not binary.is_file() or not binary.resolve().is_relative_to(expected)
            or not re.fullmatch(r'zallet_core-[a-f0-9]{16}', binary.name)):
        raise ValueError('Unexpected compiled core test executable.')
    env = {**os.environ, 'CARGO_MANIFEST_DIR': str(ROOT / 'zallet-core'),
           'CARGO_MANIFEST_PATH': str(ROOT / 'zallet-core/Cargo.toml')}
    listing = subprocess.run([str(binary), '--list', '--format', 'terse'],
                             cwd=ROOT / 'zallet-core', env=env, check=True, text=True,
                             capture_output=True, timeout=60).stdout.splitlines()
    if sum(line.endswith(': test') for line in listing) != 418 or CASE + ': test' not in listing:
        raise ValueError('Compiled test listing differs from the expected 418-test suite.')
    print('Running all 418 core tests directly after compilation.', flush=True)
    checked([str(binary)], 418, 600, env)
    print('Full core suite: 418 passed, 0 failed, 0 ignored.', flush=True)
    for attempt in range(1, 51):
        checked([str(binary), CASE, '--exact'], 1, 90, env)
        if attempt % 10 == 0:
            print('Channel-closure cleanup checks passed: ' + str(attempt) + '/50', flush=True)
    with binary.open('rb') as file:
        binary_hash = hashlib.file_digest(file, 'sha256').hexdigest()
    pathlib.Path('/out/cleanup-checks.json').write_text(json.dumps({
        'cleanupChecksPassed': 50, 'coreSuitePassed': 418,
        'compiledCoreTestSha256': binary_hash,
    }, indent=2) + '\n')
if __name__ == '__main__':
    main()
'''


def load_base():
    path = Path('/root/build-testnet-nu7-candidate.py')
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != 0:
        raise ValueError('Expected the original root-owned candidate build helper.')
    spec = importlib.util.spec_from_file_location('nu7_base', path)
    base = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(base)
    base.guard()
    if (base.ZALLET_REV != '8c5a93ba9d68ad78d199c3624413c0139781b837'
            or base.ZEBRA_REV != 'e8db87eb69254e0d4eedd058eadd48bdfa09065a'):
        raise ValueError('Unexpected pinned build source.')
    return base


def inspect(base, name):
    if name not in (CONTAINER, SOURCE_CONTAINER):
        raise ValueError('Unexpected finish container name.')
    result = subprocess.run(['docker', 'container', 'inspect', name], capture_output=True, text=True)
    if result.returncode:
        base.run('docker', 'info', '--format', '{{.ServerVersion}}', capture_output=True)
        return None
    records = json.loads(result.stdout)
    if len(records) != 1:
        raise ValueError('Unexpected container count.')
    record = records[0]
    labels = record['Config'].get('Labels', {})
    if (labels.get(base.LABEL) != base.ZALLET_REV or record.get('Mounts')
            or record['HostConfig'].get('Privileged') or record['Config'].get('User') != '10001:10001'):
        raise ValueError('Unexpected candidate identity or host access.')
    if name == CONTAINER and labels.get(FIX_LABEL) != 'closed-channel-v1':
        raise ValueError('Unexpected cleanup-observer revision.')
    if name == SOURCE_CONTAINER and labels.get('org.zec-tip-bot.test-fix') != 'b2d15011dc28a7284222f95e843880e53c4efe94':
        raise ValueError('Unexpected saved upstream test fix.')
    return record


def script(base):
    marker = "echo 'Building Zebra 7.0.0 with the wallet-required indexer feature.'"
    if base.BUILD.count(marker) != 1:
        raise ValueError('Unexpected original build recipe.')
    tail = base.BUILD[base.BUILD.index(marker):]
    tail = tail.replace("    'testsPassed': True,", "    'testsPassed': True,\n    **json.loads((out / 'test-fix.json').read_text()),\n    **json.loads((out / 'closed-channel-test-fix.json').read_text()),\n    **json.loads((out / 'cleanup-checks.json').read_text()),")
    return '''#!/bin/bash
set -euo pipefail
umask 0022
cd /work/zallet
test "$(git rev-parse HEAD)" = ''' + base.ZALLET_REV + '''
test "$(git -C /work/zebra rev-parse HEAD)" = ''' + base.ZEBRA_REV + '''
python3 /opt/candidate/verify.py
python3 /opt/finish/fix-test.py
echo 'Compiling the core tests once; compilation has no short test timeout.'
cargo +1.95.0 test --release --locked --jobs 2 -p zallet-core --lib --no-run --message-format=json > /out/core-artifacts.jsonl
python3 /opt/finish/checks.py
''' + tail


def status(base):
    record = inspect(base, CONTAINER)
    if record is None:
        print('Finish build has not started.')
        return
    state = record['State']
    print(json.dumps({'candidate': CONTAINER, 'state': state['Status'],
                      'exitCode': state.get('ExitCode') if state['Status'] == 'exited' else None,
                      'outOfMemory': state.get('OOMKilled', False), 'installed': False,
                      'nodeAndWalletServicesChanged': False}, indent=2))
    base.run('docker', 'logs', '--tail', '30', CONTAINER)
    if state['Status'] == 'exited' and state.get('ExitCode') == 0:
        out = ROOT / 'artifacts'
        if not out.exists():
            out.mkdir(mode=0o700)
            base.run('docker', 'cp', f'{CONTAINER}:/out/.', str(out))
        manifest = base.validate_manifest(out)
        if (manifest.get('closedChannelTestFix') is not True or manifest.get('localTestOnlyPatch') is not True
                or manifest.get('beforeClosedChannelFixSha256') != SOURCE_HASH
                or manifest.get('decryptorLibrarySourceSha256') != LIBRARY_HASH
                or manifest.get('cleanupChecksPassed') != 50 or manifest.get('coreSuitePassed') != 418):
            raise ValueError('Missing completed cleanup-test provenance.')
        print('BUILD VERIFIED: ' + str(out))
        print('Candidate only. Installation and current-Testnet verification are still required.')
    elif state['Status'] == 'exited':
        print('Finish build did not pass. Existing node and wallet services were unchanged.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    base = load_base()
    if ROOT.is_symlink() or (ROOT.exists() and ROOT.resolve() != ROOT):
        raise ValueError('Unexpected finish directory.')
    if args.status or inspect(base, CONTAINER) is not None:
        status(base)
        return
    source = inspect(base, SOURCE_CONTAINER)
    if (source is None or source['State']['Status'] != 'exited' or source['State'].get('ExitCode') != 1
            or source['State'].get('OOMKilled')):
        raise ValueError('Expected the stopped r4 candidate, with its compiled dependencies intact.')
    if shutil.disk_usage('/opt').free < 40 * 1024 ** 3:
        raise ValueError('Fewer than 40 GiB free; review disk space before continuing.')
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    inputs = {'Dockerfile': f'FROM {SNAPSHOT}\nCOPY finish.sh fix-test.py checks.py /opt/finish/\nENTRYPOINT ["bash", "/opt/finish/finish.sh"]\n',
              'finish.sh': script(base), 'fix-test.py': PATCHER, 'checks.py': CHECKS,
              '.dockerignore': '*\n!Dockerfile\n!finish.sh\n!fix-test.py\n!checks.py\n'}
    for name, content in inputs.items():
        target = ROOT / name
        if target.is_symlink() or (target.exists() and target.read_text() != content):
            raise ValueError('Existing finish inputs differ; refusing to overwrite.')
        if not target.exists():
            with target.open('x', encoding='utf-8', newline='\n') as file:
                file.write(content)
            target.chmod(0o644)
    print('Preserving the existing compilation cache and preparing the test-only correction.', flush=True)
    snapshot = base.run('docker', 'commit', source['Id'], SNAPSHOT, capture_output=True).stdout.strip()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', snapshot):
        raise ValueError('Unexpected saved image identity.')
    base.run('docker', 'build', '-t', IMAGE, str(ROOT))
    base.run('docker', 'run', '--detach', '--name', CONTAINER,
             '--label', base.LABEL + '=' + base.ZALLET_REV, '--label', FIX_LABEL + '=closed-channel-v1',
             '--cpus', '2', '--memory', '8g', '--memory-swap', '8g', '--pids-limit', '1024',
             '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
             '--log-opt', 'max-size=10m', '--log-opt', 'max-file=3', IMAGE, capture_output=True)
    print('Finish build is running in the background. Existing services are unchanged.')
    print('Check: python3 /root/finish-testnet-nu7-candidate.py --status')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print('Stopped: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        sys.exit(1)
