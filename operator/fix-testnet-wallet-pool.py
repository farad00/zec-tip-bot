#!/usr/bin/env python3
"""Check a quota-limited wallet pool and optionally apply a reversible service workaround."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import tomllib

UNIT = 'zec-tip-bot-wallet-testnet.service'
DATA = Path('/var/lib/zec-tip-bot-wallet-nu7-testnet')
BIN = Path('/opt/zec-tip-bot-chain/nu7-runtime-8c5a93b-r5/zallet-zebra')
DROPIN = Path('/etc/systemd/system') / (UNIT + '.d') / '40-database-pool-capacity.conf'
OVERRIDE = '# Allow RPC database connections beyond the four persistent sync tasks.\n[Service]\nCPUQuota=400%\n'
IMAGE = 'zec-nu7-finish:8c5a93b-r5'

# Only public build files are inspected, inside an isolated container without host mounts.
PUBLIC_CHECK = r'''import json, os, pathlib, re, tomllib
def uses_two_connections_per_cpu(util, pool_config):
    # Inspect executable definitions, not documentation (one upstream field's
    # comment still says *4 even though Default actually selects *2).
    util = re.sub(r'//[^\n]*', '', util)
    pool_config = re.sub(r'//[^\n]*', '', pool_config)
    cached_count = re.search(r'\bstatic\s+ref\s+CPU_COUNT\s*:\s*usize\s*=\s*num_cpus::get\(\)\s*;', util)
    double_count = re.search(r'fn\s+get_default_pool_max_size\(\)\s*->\s*usize\s*\{\s*\*CPU_COUNT\s*\*\s*2\s*\}', util)
    default_uses_count = re.search(r'impl\s+Default\s+for\s+PoolConfig\s*\{\s*fn\s+default\(\)\s*->\s*Self\s*\{\s*Self::new\(crate::util::get_default_pool_max_size\(\)\)\s*\}\s*\}', pool_config)
    return all((cached_count, double_count, default_uses_count))

root = pathlib.Path('/work/zallet')
lock = tomllib.loads((root / 'Cargo.lock').read_text())
versions = {p['name']: p['version'] for p in lock['package'] if p['name'] in ('deadpool', 'num_cpus')}
if versions != {'deadpool': '0.12.3', 'num_cpus': '1.17.0'}:
    print(json.dumps({'dependencyVersions': versions, 'requiresSeparateInspection': True}))
    raise SystemExit(0)
registries = set()
for cargo in (pathlib.Path(os.environ.get('CARGO_HOME', '/usr/local/cargo')), pathlib.Path('/home/builder/.cargo')):
    registries.update((cargo / 'registry/src').glob('*'))
def source(package, relative):
    paths = [base / package / relative for base in registries if (base / package / relative).is_file()]
    if len(paths) != 1:
        raise ValueError('Expected one public dependency source')
    return paths[0].read_text()
util = source('deadpool-0.12.3', 'src/util.rs')
pool_config = source('deadpool-0.12.3', 'src/managed/config.rs')
cpu = source('num_cpus-1.17.0', 'src/linux.rs')
connection = (root / 'zallet-core/src/components/database/connection.rs').read_text()
sync = (root / 'zallet-core/src/components/sync.rs').read_text()
spawn = sync.split('pub(crate) async fn spawn<C: Chain>', 1)[1].split('\n}\n', 1)[0]
checks = {
 'defaultPoolUsesTwoConnectionsPerCpu': uses_two_connections_per_cpu(util, pool_config),
 'cpuDetectionUsesCgroupQuota': all(s in cpu for s in ('CgroupVersion::V2', 'self.raw_param("cpu.max")',
      'std::cmp::min(quota, logical)', 'CGROUPS_CPUS.store(count')),
 'walletUsesDefaultPool': '.config(deadpool::managed::PoolConfig::default())' in connection,
 'fourPersistentSyncHandles': spawn.count('db.handle().await?') == 4
     and all(s in spawn for s in ('Batch decryptor', 'Steady state', 'Recover history', 'Data requests')),
}
print(json.dumps({'dependencyVersions': versions, **checks}))
'''


def run(*args, timeout=30):
    try:
        return subprocess.run(args, check=True, text=True, capture_output=True, timeout=timeout).stdout.strip()
    except subprocess.CalledProcessError as error:
        # Never stringify CalledProcessError: its argv includes the entire source-check program.
        report = {'failedTool': args[0], 'exitCode': error.returncode}
        if args[0] == 'docker':
            # This helper's sole Docker invocation reads public compiler sources in an
            # isolated build image, with no wallet files, host mounts or credentials.
            report['publicBuildDiagnostic'] = (error.stderr or '')[-2500:]
        raise ValueError(json.dumps(report)) from None


def load_helper(filename, name):
    import importlib.util
    path = Path('/root') / filename
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Expected a protected root-owned setup helper.')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def public_evidence():
    raw = run('docker', 'run', '--rm', '--network', 'none', '--cap-drop', 'ALL',
              '--security-opt', 'no-new-privileges', '--memory', '256m', '--pids-limit', '64',
              '--entrypoint', 'python3', IMAGE, '-c', PUBLIC_CHECK, timeout=45)
    if len(raw) > 4096:
        raise ValueError('Unexpected public dependency report size.')
    result = json.loads(raw)
    fields = ('defaultPoolUsesTwoConnectionsPerCpu', 'cpuDetectionUsesCgroupQuota',
              'walletUsesDefaultPool', 'fourPersistentSyncHandles')
    if any(result.get(key) is not True for key in fields):
        print(json.dumps({'publicSourceEvidence': result}), flush=True)
        raise ValueError('Public source checks did not establish the quota/pool relationship. No service change made.')
    return result


def quota_cpus(raw):
    fields = raw.split()
    if len(fields) != 2 or not all(value.isdecimal() for value in fields):
        raise ValueError('Expected a finite cgroup-v2 CPU quota.')
    quota, period = map(int, fields)
    if quota <= 0 or period <= 0:
        raise ValueError('Expected positive CPU quota values.')
    return math.ceil(quota / period)


def service_capacity():
    if run('systemctl', 'is-active', UNIT) != 'active':
        raise ValueError('Wallet service must be active before this check.')
    pid_text = run('systemctl', 'show', UNIT, '--property=MainPID', '--value')
    if not pid_text.isdecimal() or int(pid_text) <= 1:
        raise ValueError('Expected the active wallet process.')
    pid = int(pid_text)
    if Path(f'/proc/{pid}/exe').resolve() != BIN:
        raise ValueError('Expected the installed NU7 candidate wallet process.')
    group = run('systemctl', 'show', UNIT, '--property=ControlGroup', '--value')
    if group != '/system.slice/' + UNIT:
        raise ValueError('Expected the dedicated wallet cgroup.')
    cpu_limit = quota_cpus((Path('/sys/fs/cgroup') / group.lstrip('/') / 'cpu.max').read_text())
    affinity = len(os.sched_getaffinity(pid))
    cpus = min(cpu_limit, affinity)
    if affinity < 4:
        raise ValueError('At least four CPUs must be available to this wallet process.')
    return {'cpuQuotaCores': cpu_limit, 'availableAffinityCores': affinity,
            'inferredPoolCapacity': cpus * 2, 'persistentSyncConnections': 4}


def verify_testnet(check):
    config = tomllib.loads((DATA / 'zallet.toml').read_text())
    if (config.get('backend') != 'zebra' or config.get('consensus', {}).get('network') != 'test'
            or config.get('external', {}).get('broadcast') is not False
            or config.get('keystore', {}).get('require_backup') is not True
            or config.get('rpc', {}).get('async_operation_limit') != 0):
        raise ValueError('Expected the existing guarded Testnet staging configuration.')
    node = check.rpc(18232, check.cookie_file(Path('/var/lib/zec-tip-bot-chain-testnet/rpc')),
                     'getblockchaininfo')
    if not isinstance(node, dict) or node.get('chain') != 'test':
        raise ValueError('The local node must report Testnet.')


def check_dropin():
    if DROPIN.parent.is_symlink() or DROPIN.is_symlink():
        raise ValueError('Service override paths must not be symbolic links.')
    if DROPIN.exists():
        info = DROPIN.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Expected a protected service override.')
        if DROPIN.read_text() != OVERRIDE:
            raise ValueError('A different service override exists; it was preserved.')
        return True
    return False


def apply_workaround(inspection, check):
    if check_dropin():
        raise ValueError('The capacity override already exists; use the read-only check.')
    # Exclusive creation preserves any intervening administrator change.
    with DROPIN.open('x', encoding='utf-8') as file:
        file.write(OVERRIDE)
    DROPIN.chmod(0o644)
    try:
        run('systemctl', 'daemon-reload')
        run('systemctl', 'restart', UNIT, timeout=45)
        time.sleep(8)
        capacity = service_capacity()
        if capacity['cpuQuotaCores'] != 4 or capacity['inferredPoolCapacity'] != 8:
            raise ValueError('The expected increased pool capacity was not established.')
        reads = []
        for _ in range(2):
            result = inspection.read_status(check)
            reads.append(result)
            if result.get('walletStatusRead') != 'available':
                raise ValueError('Wallet status still did not respond after the quota change.')
            time.sleep(2)
        return {'workaroundApplied': True, 'walletRestarted': True, 'capacityAfter': capacity,
                'consecutiveStatusReads': reads, 'rollBackPerformed': False}
    except Exception as error:
        # Remove only our exact new file, then restore the previous unit configuration.
        if DROPIN.is_symlink() or not DROPIN.exists() or DROPIN.read_text() != OVERRIDE:
            raise ValueError('Capacity check failed; service override changed externally. Manual inspection needed.') from None
        DROPIN.unlink()
        run('systemctl', 'daemon-reload')
        run('systemctl', 'restart', UNIT, timeout=45)
        return {'workaroundApplied': False, 'walletRestarted': True, 'rollBackPerformed': True,
                'failureCategory': type(error).__name__}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Apply only after all diagnosis guards pass.')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Run this in the existing VPS root terminal.')
    base = load_helper('build-testnet-nu7-candidate.py', 'pool_build_guard')
    base.guard()
    inspection = load_helper('inspect-testnet-wallet.py', 'pool_inspection')
    check = load_helper('check-testnet-wallet.py', 'pool_read_check')
    verify_testnet(check)
    report = {'createsKeysOrAccounts': False, 'spendingEnabled': False,
              'changesNodeOrBotConfiguration': False, 'currentTestnetTipVerified': False}
    report['publicSourceEvidence'] = public_evidence()
    report['capacityBefore'] = service_capacity()
    report['statusBefore'] = inspection.read_status(check)
    existing = check_dropin()
    if args.apply and report['statusBefore'].get('walletStatusRead') != 'available':
        if (existing or report['capacityBefore']['cpuQuotaCores'] != 2
                or report['statusBefore'].get('statusErrorCategory') != 'TimeoutError'):
            raise ValueError('The exact two-CPU/four-connection condition was not established; no change made.')
        print('Source and CPU-quota checks passed. Testing a four-CPU allowance for the wallet only...', flush=True)
        report.update(apply_workaround(inspection, check))
    else:
        report.update({'workaroundApplied': existing, 'walletRestarted': False})
    print(json.dumps(report, indent=2))
    print('No seed, account, deposit address or transfer was created. Node and bot configuration unchanged.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print('Pool check stopped: ' + (str(error) if isinstance(error, ValueError)
                                     else type(error).__name__), file=sys.stderr)
        sys.exit(1)
