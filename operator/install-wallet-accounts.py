#!/usr/bin/env python3
"""Install private Testnet account setup only; preserve all signing and funding holds."""
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import time
import uuid
import zipfile

APP = Path('/srv/zec-tip-bot')
ARCHIVE = Path('/root/zec-wallet-accounts-update.zip')
ARCHIVE_SHA = 'ed60bdfb9bd5338702c45d47489208e0c566ae0777aed9881cb218d034a028ed'
BROKER = Path('/opt/zec-tip-bot-chain/wallet-account-broker.py')
BROKER_CONFIG = Path('/etc/zec-tip-bot-wallet-broker.json')
BROKER_DATA = Path('/var/lib/zec-tip-bot-wallet-broker')
BROKER_RUN = Path('/run/zec-tip-bot-wallet-broker')
UNIT = Path('/etc/systemd/system/zec-tip-bot-wallet-accounts.service')
GROUP = 'zectip-wallet-client'
FILES = {'Dockerfile', 'src/account.ts', 'src/config.ts', 'src/deployment.ts',
    'src/wallet-accounts.ts', 'src/wallet-account-client.ts',
    'test/private-tips.test.ts', 'test/wallet-accounts.test.ts', 'test/wallet-account-client.test.ts',
    'deploy/wallet-account-broker.py'}
PREFLIGHT_JS = '''if(process.env.BOT_MODE!=="serve" || process.env.PRIVATE_TIPPING_ENABLED!=="true" || process.env.ZCASH_NETWORK!=="testnet" || process.env.X_POSTING_ENABLED!=="false" || process.env.HOSTED_WALLETS_ENABLED!=="false")process.exit(1);'''
SOCKET_CHECK_JS = '''import {WalletAccountClient} from "./dist/src/wallet-account-client.js";
const health=await new WalletAccountClient(process.env.WALLET_BROKER_SOCKET).health();
console.log(JSON.stringify(health));
if(health.network!=="testnet"||!health.accountCreationReady||!health.backupConfirmed||health.depositsEnabled||health.transfersEnabled)process.exit(1);'''


def payload(raw):
    if hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA:
        raise ValueError('Update archive checksum did not match; no installation started.')
    output = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if len(archive.namelist()) != len(FILES) or set(archive.namelist()) != FILES:
            raise ValueError('Unexpected account-update file list.')
        for name in FILES:
            info = archive.getinfo(name)
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or not 0 < info.file_size <= 100_000:
                raise ValueError('Unexpected update entry.')
            output[name] = archive.read(name)
    return output


def compose_update(original, group):
    if (not isinstance(original, dict) or original.get('name') != 'zec-tip-bot'
            or set(original.get('services', {})) != {'bot', 'proxy'}):
        raise ValueError('Expected the existing two-service deployment.')
    updated = copy.deepcopy(original)
    bot = updated['services']['bot']
    if bot.get('ports') or bot.get('privileged'):
        raise ValueError('The bot must remain private behind the existing proxy.')
    volumes = bot.setdefault('volumes', [])
    expected = BROKER_RUN.as_posix() + ':/run/zec-tip-bot-wallet-broker:ro'
    for mount in volumes:
        if not isinstance(mount, str):
            raise ValueError('Unexpected volume format; existing deployment preserved.')
        if '/run/zec-tip-bot-wallet-broker' in mount and mount != expected:
            raise ValueError('A different wallet-broker mount already exists.')
        if any(secret in mount for secret in ('wallet-nu7-testnet', 'wallet-testnet', 'encryption-identity')):
            raise ValueError('Wallet data must never be mounted into the web container.')
    if expected not in volumes: volumes.append(expected)
    groups = bot.setdefault('group_add', [])
    if not isinstance(groups, list) or any(not str(item).isdigit() for item in groups):
        raise ValueError('Unexpected container group configuration.')
    if str(group) not in [str(item) for item in groups]: groups.append(str(group))
    env = bot.setdefault('environment', {})
    if not isinstance(env, dict): raise ValueError('Unexpected container environment configuration.')
    env.update(WALLET_ACCOUNTS_ENABLED='true', WALLET_BROKER_SOCKET='/run/zec-tip-bot-wallet-broker/wallet.sock')
    return updated


def unit_text():
    return f'''[Unit]
Description=Private Testnet wallet account setup (no transfers)
Wants=zec-tip-bot-wallet-testnet.service
After=zec-tip-bot-wallet-testnet.service
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=zectip-chain
Group={GROUP}
ExecStart=/usr/bin/python3 {BROKER}
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
RuntimeDirectory=zec-tip-bot-wallet-broker
RuntimeDirectoryMode=0750
StateDirectory=zec-tip-bot-wallet-broker
StateDirectoryMode=0700
UMask=0077
MemoryMax=256M
CPUQuota=100%
TasksMax=64
LimitNOFILE=256
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths={BROKER_DATA} {BROKER_RUN}
ReadOnlyPaths=/var/lib/zec-tip-bot-wallet-nu7-testnet
InaccessiblePaths=/var/lib/zec-tip-bot-wallet-nu7-testnet/encryption-identity.txt
CapabilityBoundingSet=
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
IPAddressDeny=any
IPAddressAllow=localhost

[Install]
WantedBy=multi-user.target
'''


def compose(*args, **kwargs):
    return subprocess.run(['docker','compose','--env-file','.env','-f','deploy/compose.yaml',*args],
                          cwd=APP, check=True, **kwargs)


def private_context():
    import importlib.util
    path = Path('/root/fix-testnet-wallet-pool.py')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Expected the protected existing pool helper.')
    spec = importlib.util.spec_from_file_location('account_install_pool', path)
    pool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pool)
    base = pool.load_helper('build-testnet-nu7-candidate.py','account_install_build')
    base.guard()
    check = pool.load_helper('check-testnet-wallet.py','account_install_check')
    pool.verify_testnet(check)
    pool.service_capacity()
    recovery = pool.load_helper('prepare-testnet-recovery.py','account_install_recovery')
    state = recovery.private_state()
    current = recovery.metadata(recovery.DATA / 'wallet.db')
    if (not state or state.get('nativeBackupConfirmed') is not True
            or state.get('localSeedRecoveryVerified') is not True or not current['confirmed']
            or current['seeds'] != 1 or current['fingerprint'] != state.get('fingerprint')):
        raise ValueError('Native backup confirmation and local recovery must be verified first.')
    activation = pool.load_helper('activate-testnet-nu7-node.py','account_install_node')
    build = base.validate_manifest(activation.ARTIFACTS)
    activation.require_build_metadata(build)
    if recovery.file_digest(recovery.BIN) != build['binaries']['zallet-zebra']:
        raise ValueError('Installed native wallet differs from the verified build.')
    return pool, recovery, state


def main():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Run this installer in the existing VPS root terminal.')
    os.umask(0o077)
    pool, recovery, state = private_context()
    files = payload(ARCHIVE.read_bytes())
    if APP.is_symlink() or APP.resolve() != APP or not (APP / '.env').is_file():
        raise ValueError('Expected the existing app deployment.')
    compose('config','--quiet')
    compose('exec','-T','bot','node','-e',PREFLIGHT_JS)
    import grp
    import pwd
    try:
        group = grp.getgrnam(GROUP)
    except KeyError:
        subprocess.run(['groupadd','--system',GROUP], check=True)
        group = grp.getgrnam(GROUP)
    user = pwd.getpwnam('zectip-chain')
    if not 100 <= group.gr_gid <= 65535:
        raise ValueError('Unexpected private client group.')
    config = {'network':'testnet','sourceRevision':recovery.REVISION,
              'seedFingerprintHex':state['fingerprint'],'localSeedRecoveryVerified':True,'clientGroup':group.gr_gid}
    if BROKER_CONFIG.is_symlink() or (BROKER_CONFIG.exists() and json.loads(BROKER_CONFIG.read_text()) != config):
        raise ValueError('An existing broker configuration differs; it was preserved.')
    if UNIT.is_symlink() or (UNIT.exists() and UNIT.read_text() != unit_text()):
        raise ValueError('An existing broker unit differs; it was preserved.')
    for directory in (BROKER_DATA, BROKER_RUN):
        if directory.is_symlink(): raise ValueError('Broker directories must not be symbolic links.')
        directory.mkdir(mode=0o700 if directory==BROKER_DATA else 0o750, exist_ok=True)
        os.chown(directory,user.pw_uid,group.gr_gid)
        directory.chmod(0o700 if directory==BROKER_DATA else 0o750)
    BROKER_CONFIG.write_text(json.dumps(config,indent=2)+'\n')
    BROKER_CONFIG.chmod(0o640)
    os.chown(BROKER_CONFIG,0,group.gr_gid)
    BROKER.write_bytes(files['deploy/wallet-account-broker.py'])
    BROKER.chmod(0o644)
    result = subprocess.run(['/usr/bin/python3',str(BROKER),'--check'], capture_output=True, text=True,
        user=user.pw_uid, group=group.gr_gid, extra_groups=[], timeout=30)
    health = json.loads(result.stdout) if result.returncode==0 else {}
    print(json.dumps({'accountBrokerReadiness':health}),flush=True)
    if not health.get('accountCreationReady') or health.get('transfersEnabled') is not False:
        raise ValueError('Account service is not ready. Website, node and wallet services were not changed.')
    UNIT.write_text(unit_text())
    UNIT.chmod(0o644)
    subprocess.run(['systemd-analyze','verify',str(UNIT)],check=True)
    pool.run('systemctl','daemon-reload')
    pool.run('systemctl','enable','--now',UNIT.name)
    # Broker is installed before touching the web app; it still has no transfer API.
    for _ in range(10):
        if (BROKER_RUN/'wallet.sock').exists(): break
        time.sleep(1)
    else: raise ValueError('Private broker socket did not appear. Website preserved.')
    compose_file = APP/'deploy/compose.yaml'
    original = compose_file.read_bytes()
    updated = compose_update(json.loads(original),group.gr_gid)
    backup = APP/'.update-backups'/('wallet-accounts-'+uuid.uuid4().hex)
    backup.mkdir(parents=True,mode=0o700)
    old_image = subprocess.run(['docker','image','inspect','--format','{{.Id}}','zec-tip-bot:local'],
                               check=True,capture_output=True,text=True).stdout.strip()
    old_tag = 'zec-tip-bot:before-accounts-'+uuid.uuid4().hex[:12]
    pool.run('docker','tag',old_image,old_tag)
    absent = []
    for name in [*files,'deploy/compose.yaml']:
        target = APP/name
        if target.is_symlink() or not target.resolve().is_relative_to(APP):
            raise ValueError('Update target leaves the prepared app directory.')
        if target.exists():
            saved = backup/name
            saved.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(target,saved)
        else: absent.append(name)
    started = False
    try:
        print('Building account-only website update and running its offline tests...',flush=True)
        for name,content in files.items():
            target=APP/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(content);target.chmod(0o644)
        compose('build','bot')
        compose_file.write_text(json.dumps(updated,indent=2)+'\n');compose_file.chmod(0o644)
        compose('config','--quiet')
        compose('run','--rm','--no-deps','bot','node','scripts/check-deployment.mjs')
        started=True
        compose('up','-d','--no-deps','--force-recreate','bot')
        compose('exec','-T','bot','node','--input-type=module','-e',SOCKET_CHECK_JS)
        for _ in range(8):
            result=subprocess.run(['curl','--fail','--silent','--show-error','--max-time','8',
                                  'https://178-18-247-12.sslip.io/healthz'],capture_output=True)
            if result.returncode==0: break
            time.sleep(2)
        else: raise ValueError('External website health check failed.')
    except BaseException:
        print('Restoring the previous web image and source files. Wallet data is preserved.',flush=True)
        for name in [*files,'deploy/compose.yaml']:
            target=APP/name;saved=backup/name
            if saved.exists(): shutil.copy2(saved,target)
            elif name in absent and target.exists() and target.read_bytes()==files.get(name): target.unlink()
        pool.run('docker','tag',old_tag,'zec-tip-bot:local')
        if started: compose('up','-d','--no-deps','--force-recreate','bot')
        raise
    print(json.dumps({'walletAccountSetupInstalled':True,'network':'testnet',
        'accountsCreatedByInstaller':False,'depositsEnabled':False,'transfersEnabled':False,
        'postingChanged':False,'privateEnvironmentChanged':False,'codeBackup':str(backup)},indent=2))
    print('Sign in again at https://178-18-247-12.sslip.io/account, then choose Create my Testnet wallet.')


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('Account update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__),file=sys.stderr)
        raise SystemExit(1)
