#!/usr/bin/env python3
"""Install a pinned, presentation-only dashboard update. Preserve wallet and spending controls."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
import zipfile

APP=Path('/srv/zec-tip-bot')
ARCHIVE=Path('/root/zec-dashboard-update.zip')
ARCHIVE_SHA='8e1153ae1366c2216eb09b02eec183a93e1eaa39a32149d6b14d4bdf5ca50d6d'
FILES={'Dockerfile','src/account.ts','src/account-ui.ts','src/wallet-health.ts','test/account-ui.test.ts','test/wallet-health.test.ts'}
CONTROL=Path('/etc/zec-tip-bot-wallet-transfers.json')
PREFLIGHT='''if(process.env.BOT_MODE!=="serve"||process.env.PRIVATE_TIPPING_ENABLED!=="true"||process.env.ZCASH_NETWORK!=="testnet"||process.env.X_POSTING_ENABLED!=="false"||process.env.HOSTED_WALLETS_ENABLED!=="false"||process.env.WALLET_ACCOUNTS_ENABLED!=="true")process.exit(1);'''

def payload(raw):
    if hashlib.sha256(raw).hexdigest()!=ARCHIVE_SHA:raise ValueError('Dashboard release checksum did not match.')
    result={}
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        if len(zipped.namelist())!=len(FILES) or set(zipped.namelist())!=FILES:raise ValueError('Unexpected dashboard file list.')
        for name in FILES:
            entry=zipped.getinfo(name);path=PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or not 0<entry.file_size<=100000:raise ValueError('Unexpected dashboard entry.')
            result[name]=zipped.read(name)
    return result

def run(args,quiet=True,timeout=900):
    return subprocess.run(args,cwd=APP,check=True,capture_output=quiet,text=True,timeout=timeout)

def compose(*args,quiet=True):
    return run(['docker','compose','--env-file','.env','-f','deploy/compose.yaml',*args],quiet=quiet)

def protected_files():
    paths=[APP/'.env',APP/'deploy/compose.yaml',CONTROL,
           Path('/etc/systemd/system/zec-tip-bot-wallet-testnet.service'),
           Path('/etc/systemd/system/zec-tip-bot-wallet-accounts.service')]
    result={}
    for path in paths:
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected protected deployment files.')
        result[path]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result

def broker_health():
    path=Path('/root/enable-testnet-deposits.py');info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected the installed private readiness helper.')
    spec=importlib.util.spec_from_file_location('dashboard_health',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    value=module.read('/health')
    if value.get('network')!='testnet' or value.get('transfersEnabled') is not False:raise ValueError('The Testnet transfer hold must remain in place.')
    return value

def wait_web():
    for _ in range(20):
        try:
            result=compose('exec','-T','bot','node','--input-type=module','-e',
                'const r=await fetch("http://127.0.0.1:3001/healthz");if(!r.ok)process.exit(1);')
            if result.returncode==0:return
        except subprocess.SubprocessError:pass
        time.sleep(2)
    raise ValueError('The updated website did not pass its local health check.')

def install(files):
    baseline=protected_files();before=broker_health()
    compose('exec','-T','bot','node','-e',PREFLIGHT)
    previous=run(['docker','image','inspect','--format','{{.Id}}','zec-tip-bot:local']).stdout.strip()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',previous):raise ValueError('Could not identify the previous web image.')
    backup=APP/'.update-backups'/('dashboard-'+uuid.uuid4().hex);backup.mkdir(parents=True,mode=0o700)
    old_tag='zec-tip-bot:before-dashboard-'+uuid.uuid4().hex[:12]
    run(['docker','tag',previous,old_tag]);absent=[]
    for name in files:
        target=APP/name
        if target.is_symlink() or not target.resolve().is_relative_to(APP):raise ValueError('Dashboard target leaves the app directory.')
        if target.exists():
            saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,saved)
        else:absent.append(name)
    attempted_restart=False
    try:
        for name,content in files.items():
            target=APP/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content);target.chmod(0o644)
        print('Building the visual dashboard and running the application tests...',flush=True)
        compose('build','bot',quiet=False)
        compose('run','--rm','--no-deps','bot','node','scripts/check-deployment.mjs',quiet=False)
        if protected_files()!=baseline:raise ValueError('Deployment controls changed during the build; stopping before restart.')
        attempted_restart=True;compose('up','-d','--no-deps','--force-recreate','bot',quiet=False)
        wait_web();compose('exec','-T','bot','node','-e',PREFLIGHT)
        after=broker_health()
        if protected_files()!=baseline or after.get('depositsEnabled')!=before.get('depositsEnabled'):
            raise ValueError('Wallet controls differed after the visual update.')
    except BaseException:
        print('Restoring the previous website image and source. Wallet services were not changed.',flush=True)
        for name,content in files.items():
            target=APP/name;saved=backup/name
            if saved.exists():shutil.copy2(saved,target)
            elif name in absent and target.exists() and target.read_bytes()==content:target.unlink()
        run(['docker','tag',old_tag,'zec-tip-bot:local'])
        if attempted_restart:compose('up','-d','--no-deps','--force-recreate','bot',quiet=False)
        raise
    return {'visualDashboardInstalled':True,'network':'testnet','privateDepositQr':True,
            'walletServicesChanged':False,'privateEnvironmentChanged':False,
            'depositsEnabled':after.get('depositsEnabled') is True,'transfersEnabled':False,
            'postingChanged':False,'codeBackup':str(backup)}

def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    os.umask(0o077);files=payload(ARCHIVE.read_bytes());print(json.dumps(install(files),indent=2))
    print('Refresh your account page to see the new dashboard. Funding checks continue separately.')

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Dashboard update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__),file=sys.stderr)
        raise SystemExit(1)
