#!/usr/bin/env python3
"""Install the tested Testnet transfer adapter with every funding/spending switch held."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
import uuid
import zipfile

APP=Path('/srv/zec-tip-bot')
ARCHIVE=Path('/root/zec-wallet-transfers-held.zip')
ARCHIVE_SHA='5073665dda6fce4b60cd01cf6c92a2579d4c6666f2ae9f4cde229203c393ea55'
ENTRY=Path('/opt/zec-tip-bot-chain/wallet-transfer-broker.py')
FILES={'Dockerfile','src/account.ts','src/cli.ts','src/config.ts','src/deployment.ts','src/tip-store.ts',
       'src/tip-engine.ts','src/wallet-transfer-client.ts','src/wallet-worker.ts',
       'test/wallet-transfers.test.ts','test/wallet-test-address.ts','deploy/wallet-transfer-broker.py',
       'deploy/wallet-account-broker.py'}
CHECK='''import {WalletAccountClient} from "./dist/src/wallet-account-client.js";
const client=new WalletAccountClient(process.env.WALLET_BROKER_SOCKET),health=await client.health();
if(!health.synced||!health.currentConsensusVerified||!health.backupConfirmed||health.depositsEnabled||health.transfersEnabled)process.exit(1);
const accounts=await client.accounts();for(const account of accounts)await client.balance(account.account);
console.log(JSON.stringify({network:"testnet",synced:true,privateBalancesChecked:accounts.length,depositsEnabled:false,transfersEnabled:false}));'''


def payload(raw):
    if hashlib.sha256(raw).hexdigest()!=ARCHIVE_SHA:raise ValueError('Transfer update archive checksum did not match.')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if len(archive.namelist())!=len(FILES) or set(archive.namelist())!=FILES:raise ValueError('Unexpected transfer update file list.')
        result={}
        for name in FILES:
            entry=archive.getinfo(name)
            if not 0<entry.file_size<=100_000:raise ValueError('Unexpected update file size.')
            result[name]=archive.read(name)
        return result


def timeout_config(raw):
    before=tomllib.loads(raw.decode())
    if (before.get('consensus',{}).get('network')!='test' or before.get('external',{}).get('broadcast') is not False
            or before.get('keystore',{}).get('require_backup') is not True or before.get('rpc',{}).get('async_operation_limit')!=0):
        raise ValueError('Existing native wallet guards differ. Configuration preserved.')
    existing=before.get('rpc',{}).get('timeout')
    if existing==300:return raw
    if existing is not None:raise ValueError('Unexpected existing native RPC timeout. Configuration preserved.')
    text=raw.decode();pattern=r'(?m)^\[rpc\][ \t]*$'
    if len(re.findall(pattern,text))!=1:raise ValueError('Expected one native RPC configuration section.')
    updated=re.sub(pattern,'[rpc]\ntimeout = 300',text,count=1).encode()
    expected=json.loads(json.dumps(before));expected['rpc']['timeout']=300
    if tomllib.loads(updated.decode())!=expected:raise ValueError('Wallet configuration changed outside its RPC timeout.')
    return updated


def load_base():
    path=Path('/root/update-wallet-balances.py');info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected the protected installed balance helper.')
    spec=importlib.util.spec_from_file_location('held_install_base',path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    return old


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    os.umask(0o077)
    old=load_base()
    pool,recovery,state=old.private_context();files=payload(ARCHIVE.read_bytes())
    old.compose('exec','-T','bot','node','-e',old.PREFLIGHT_JS)
    control=Path('/etc/zec-tip-bot-wallet-transfers.json')
    if control.exists():
        value=json.loads(control.read_text())
        if value.get('enabled') is not False or value.get('depositsEnabled') is not False:raise ValueError('This installer requires deposits and transfers to be held.')
    if APP.is_symlink() or APP.resolve()!=APP:raise ValueError('Expected the existing app directory.')
    previous_unit=old.UNIT.read_bytes()
    desired_unit=old.unit_text().replace('ExecStart=/usr/bin/python3 '+str(old.BROKER),'ExecStart=/usr/bin/python3 '+str(ENTRY)).encode()
    if old.UNIT.is_symlink() or previous_unit not in (old.unit_text().encode(),desired_unit):raise ValueError('Existing wallet account service differs. It was preserved.')
    wallet_config=recovery.DATA/'zallet.toml'
    if wallet_config.is_symlink():raise ValueError('Expected the existing native wallet configuration.')
    previous_config=wallet_config.read_bytes();desired_config=timeout_config(previous_config)
    backup=APP/'.update-backups'/('transfers-held-'+uuid.uuid4().hex);backup.mkdir(parents=True,mode=0o700)
    old_image=subprocess.run(['docker','image','inspect','--format','{{.Id}}','zec-tip-bot:local'],check=True,text=True,capture_output=True).stdout.strip()
    tag='zec-tip-bot:before-transfers-'+uuid.uuid4().hex[:12];pool.run('docker','tag',old_image,tag)
    saved={};absent=[]
    for name in files:
        target=APP/name
        if target.is_symlink() or not target.resolve().is_relative_to(APP):raise ValueError('Update target leaves the prepared app directory.')
        if target.exists():
            destination=backup/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,destination)
        else:absent.append(name)
    for path in (old.BROKER,ENTRY,old.UNIT,wallet_config):
        if path.is_symlink():raise ValueError('Protected service files must not be symbolic links.')
        saved[path]=path.read_bytes() if path.exists() else None
    (backup/'previous-broker.py').write_bytes(saved[old.BROKER])
    (backup/'previous-unit.service').write_bytes(previous_unit)
    (backup/'previous-zallet.toml').write_bytes(previous_config)
    changed_native=False;changed_broker=False;changed_web=False
    try:
        print('Building the private transfer update with all spending switches held...',flush=True)
        for name,content in files.items():
            target=APP/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content);target.chmod(0o644)
        old.compose('build','bot')
        old.compose('run','--rm','--no-deps','bot','node','scripts/check-deployment.mjs')
        if desired_config!=previous_config:
            changed_native=True;wallet_config.write_bytes(desired_config)
            pool.run('systemctl','restart','zec-tip-bot-wallet-testnet.service',timeout=45)
        changed_broker=True
        old.BROKER.write_bytes(files['deploy/wallet-account-broker.py']);old.BROKER.chmod(0o644)
        ENTRY.write_bytes(files['deploy/wallet-transfer-broker.py']);ENTRY.chmod(0o644)
        old.UNIT.write_bytes(desired_unit);old.UNIT.chmod(0o644)
        subprocess.run(['systemd-analyze','verify',str(old.UNIT)],check=True,capture_output=True)
        pool.run('systemctl','daemon-reload');pool.run('systemctl','restart',old.UNIT.name,timeout=45)
        changed_web=True;old.compose('up','-d','--no-deps','--force-recreate','bot')
        for _ in range(12):
            result=subprocess.run(['docker','compose','--env-file','.env','-f','deploy/compose.yaml','exec','-T','bot','node','--input-type=module','-e',CHECK],cwd=APP,capture_output=True,text=True,timeout=70)
            if result.returncode==0:
                report=json.loads(result.stdout)
                if report.get('privateBalancesChecked',0)>=2:break
            time.sleep(5)
        else:raise ValueError('Wallet did not return to synchronized, held readiness.')
        result=subprocess.run(['curl','--fail','--silent','--show-error','--max-time','8','https://178-18-247-12.sslip.io/healthz'],capture_output=True)
        if result.returncode:raise ValueError('Website health check failed.')
        print(json.dumps({'testnetTransferAdapterInstalled':True,'walletAccountsPreserved':report['privateBalancesChecked'],
            'nativeRpcTimeoutSeconds':300,'depositsEnabled':False,'transfersEnabled':False,'postingChanged':False,
            'privateEnvironmentChanged':False,'noSigningOrBroadcastDuringInstallation':True,'codeBackup':str(backup)},indent=2))
    except BaseException:
        print('Restoring the previous web image and service configuration. Wallet data is preserved.',flush=True)
        for name in files:
            target=APP/name;source=backup/name
            if source.exists():shutil.copy2(source,target)
            elif name in absent and target.exists() and target.read_bytes()==files[name]:target.unlink()
        for path,content in saved.items():
            if content is not None:path.write_bytes(content)
            elif path.exists():path.unlink()
        if changed_native:pool.run('systemctl','restart','zec-tip-bot-wallet-testnet.service',timeout=45)
        if changed_broker:pool.run('systemctl','daemon-reload');pool.run('systemctl','restart',old.UNIT.name,timeout=45)
        pool.run('docker','tag',tag,'zec-tip-bot:local')
        if changed_web or changed_broker:old.compose('up','-d','--no-deps','--force-recreate','bot')
        raise


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Held transfer installation stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
