#!/usr/bin/env python3
"""Install owner-only live wallet views, receipt deduplication and precise funds failures."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path('/root')
APP = Path('/srv/zec-tip-bot')
ARCHIVE = ROOT / 'zec-wallet-live.zip'
ARCHIVE_SHA = '42f600fb0f9a21878e901a5279b98ad3a4424997e54790a858a1a4d5bc1bb8ca'
OLD_TRANSFER_SHA = '546f01f1fb34be50ec275ab273c3fbdfabc7b4545cc272930982f9218d205c43'
PINS = {'update-visible-settings.py':'2914c6a38df74497883ab7adb3300851999523cc57badc7e98df1cb1c47ba4bf',
    'remove-signup-cap.py':'f0e504e9a7d8f9311b0fff96b9427f9ebe64ec5a65d3bc50eef448016b09503b',
    'install-public-enrollment.py':'01a7df4977611ed2dc1e70ab873fa6bbf6070407fb59b9af20f557d3272efdb6',
    'update-confirming-balance.py':'295c5d9661d2d5df89bada1935766437d6511b4e8b2b9e05620589b14e6f6c6c'}
WEB_FILES = {'src/account.ts','src/account-ui.ts','src/live-ui.ts','src/tip-store.ts','src/tip-engine.ts',
    'src/wallet-transfer-client.ts','test/account-ui.test.ts','test/wallet-live.test.ts','Dockerfile',
    'README.md','deploy/README.md','deploy/wallet-transfer-broker.py'}
NATIVE_SUPPORT = {'deploy/wallet-account-broker.py','deploy/wallet-enrollment.py',
    'operator/test_wallet_account_broker.py','operator/test_wallet_transfer_broker.py','operator/test_wallet_enrollment_broker.py'}
FILES = WEB_FILES | NATIVE_SUPPORT
PROBE = '''import {request} from "node:http";
import {loadConfig} from "./dist/src/config.js";
import {PRIVATE_JS,LEGACY_PRIVATE_JS} from "./dist/src/account-ui.js";
const c=loadConfig();if(!c.privateTips||c.network!=="testnet")process.exit(1);
const response=await new Promise((resolve,reject)=>{
 const req=request({hostname:"127.0.0.1",port:3001,path:"/account/live",headers:{host:new URL(c.baseUrl).host}},res=>{
  let body="";res.setEncoding("utf8");res.on("data",chunk=>body+=chunk);res.on("end",()=>resolve({status:res.statusCode,headers:res.headers,body}));
 });req.setTimeout(5000,()=>req.destroy(new Error("Local check timed out")));req.on("error",reject);req.end();
});
if(response.status!==401||response.headers["cache-control"]!=="no-store"||response.headers["x-robots-tag"]!=="noindex, nofollow"||JSON.parse(response.body).error!=="signin_required")process.exit(1);
if(!PRIVATE_JS.includes("/account/live")||LEGACY_PRIVATE_JS.includes("/account/live"))process.exit(1);
'''


def payload(raw):
    if hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA:
        raise ValueError('Wallet update checksum did not match.')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if set(archive.namelist()) != FILES or len(archive.namelist()) != len(FILES):
            raise ValueError('Unexpected wallet update source list.')
        if any(not 0 < archive.getinfo(name).file_size <= 100000 for name in FILES):
            raise ValueError('Unexpected source size.')
        return {name:archive.read(name) for name in FILES}


def load(name):
    path = ROOT / name
    info = path.lstat()
    if (path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]):
        raise ValueError('Expected the verified installed operator helper: ' + name)
    spec = importlib.util.spec_from_file_location('wallet_live_' + name.replace('-', '_').replace('.py', ''), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Run in the existing VPS root terminal.')
    os.umask(0o077)
    files = payload(ARCHIVE.read_bytes())
    visible = load('update-visible-settings.py')
    native_update = load('remove-signup-cap.py')
    atomic = load('install-public-enrollment.py')
    idle = load('update-confirming-balance.py')
    base = visible.load(ROOT / 'update-dashboard.py','wallet_live_base',visible.BASE_SHA)
    health = visible.load(ROOT / 'enable-testnet-deposits.py','wallet_live_health')
    atomic.protected(native_update.CONTROL)
    control_raw = native_update.CONTROL.read_bytes()
    native_update.policy(control_raw)
    native = native_update.NATIVE / 'wallet-transfer-broker.py'
    atomic.protected(native)
    old = native.read_bytes()
    if hashlib.sha256(old).hexdigest() not in {OLD_TRANSFER_SHA,hashlib.sha256(files['deploy/wallet-transfer-broker.py']).hexdigest()}:
        raise ValueError('The installed transfer broker differs from the verified deployment.')
    for name in ('wallet-account-broker.py','wallet-enrollment.py'):
        path = native_update.NATIVE / name
        atomic.protected(path)
        if path.read_bytes() != files['deploy/' + name]:
            raise ValueError('Native support source differs; no service change made.')
    idle.native_idle()
    # Synthetic candidate tests have no wallet data, RPC credentials or network.
    with tempfile.TemporaryDirectory(prefix='zec-wallet-live-tests-') as temp:
        folder = Path(temp)
        for name,raw in files.items():
            if name.startswith(('deploy/','operator/')):
                target = folder / name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(raw)
        subprocess.run(['/usr/bin/python3','-m','unittest','discover','-s','operator','-p','test_wallet_*broker.py'],cwd=folder,check=True,timeout=90)
    original_compose = base.compose
    original_wait = base.wait_web
    def wait_web():
        original_wait()
        original_compose('exec','-T','bot','node','--input-type=module','-e',PROBE)
    base.wait_web = wait_web
    base.payload = lambda raw:{name:content for name,content in payload(raw).items() if name in WEB_FILES}
    visible.ARCHIVE = ARCHIVE
    visible.ARCHIVE_SHA = ARCHIVE_SHA
    visible.FILES = WEB_FILES
    visible.IDLE = idle.IDLE
    report = visible.install(base,health)
    base.compose = original_compose
    # Stop the website, recheck idle state, update only the broker source and
    # verify that recovery/grant/payment records are byte-for-byte unchanged.
    count = native_update.activate(files,{'wallet-transfer-broker.py':old},control_raw,base,health,atomic,idle)
    base.wait_web()
    print(json.dumps({'walletLiveUpdateInstalled':True,'privateAutoRefreshSeconds':15,
        'updatesPauseInBackgroundTabs':True,'unsavedFormInputsPreserved':True,
        'insufficientFundsReasonAdded':True,'savedTipAmountsChanged':False,
        'incomingHostedWithdrawalReceiptsFixed':True,'tipReceiptsDeduplicatedPerOwner':True,
        'paymentOutcomesRemainPrivate':True,'welcomeBudgetAndRecordsPreserved':True,
        'maximumWelcomeGrants':12,'welcomeGrantsAlreadyRecorded':count,
        'workerConfigurationPreserved':report['workerConfigurationPreserved'],
        'walletOrNodeRestarted':False,'accountBrokerRestarted':True,
        'xCallsOrPaymentsRequestedByInstaller':False,'privateEnvironmentChanged':False},indent=2))
    print('Refresh each open account page once to load the new browser code. It then updates automatically while visible.')
    print('Check the existing incoming withdrawal in the recipient Activity. No new payment is needed for this check.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Wallet live update stopped: ' + (str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
