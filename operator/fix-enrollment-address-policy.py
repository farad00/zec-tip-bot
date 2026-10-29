#!/usr/bin/env python3
"""Upgrade pending Testnet setups to a fixed shielded-only receiving index."""
from contextlib import closing
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid
import zipfile

ARCHIVE=Path('/root/zec-enrollment-address-r2.zip')
ARCHIVE_SHA='a7e77c7d85ecadfc862cb7e08136be54de5eb05fec7b3ef891ba3e2432d3297c'
FILES={'src/wallet-accounts.ts','src/wallet-account-client.ts','test/wallet-accounts.test.ts','test/wallet-account-client.test.ts',
       'deploy/wallet-account-broker.py','deploy/wallet-transfer-broker.py','deploy/wallet-enrollment.py'}
NATIVE=Path('/opt/zec-tip-bot-chain')
CONTROL=Path('/etc/zec-tip-bot-wallet-transfers.json')
DATABASE=Path('/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite')
UNIT='zec-tip-bot-wallet-accounts.service'
OLD={'wallet-account-broker.py': 'f0af84bde33df5be3697369c7d805933bce3129ab04caa41bc370d1c024409b1', 'wallet-transfer-broker.py': '5ff26afb3edd89d3c0fba332a02fdc594b1d2142ab676fabae3db5db32955dc5', 'wallet-enrollment.py': 'b55a49e94994312f2be3c1a835652142cb31de0f9643483af1ed47b686a97e46'}


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the VPS root terminal.')
    os.umask(0o077)
    path=Path('/root/update-visible-settings.py')
    spec=importlib.util.spec_from_file_location('address_fix_visible',path);visible=importlib.util.module_from_spec(spec);spec.loader.exec_module(visible)
    base=visible.load(Path('/root/update-dashboard.py'),'address_fix_base',visible.BASE_SHA)
    health=visible.load(Path('/root/enable-testnet-deposits.py'),'address_fix_health')
    original=visible.load(Path('/root/install-public-enrollment.py'),'address_fix_atomic')
    raw=ARCHIVE.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ARCHIVE_SHA:raise ValueError('Address update checksum did not match.')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if set(archive.namelist())!=FILES or len(archive.namelist())!=len(FILES):raise ValueError('Unexpected address update file list.')
        files={name:archive.read(name) for name in FILES}
    old={}
    for name,sha in OLD.items():
        target=NATIVE/name;original.protected(target);content=target.read_bytes()
        if hashlib.sha256(content).hexdigest()!=sha:raise ValueError('Native source differs from the installed enrollment release.')
        old[name]=content
    original.protected(CONTROL);control=CONTROL.read_bytes()
    before=health.read('/health')
    if before.get('transfersEnabled') is not True or before.get('accountAdmissionRequired') is not True:raise ValueError('Wait for the existing Testnet backend.')
    with closing(sqlite3.connect('file:'+str(DATABASE)+'?mode=ro',uri=True)) as db:
        if db.execute('SELECT COUNT(*) FROM enrollment_bindings').fetchone()[0] or db.execute('SELECT COUNT(*) FROM welcome_grants').fetchone()[0]:
            raise ValueError('This repair applies before any new admission or grant. Existing records were preserved.')
    compose=base.compose
    visible.ARCHIVE=ARCHIVE;visible.ARCHIVE_SHA=ARCHIVE_SHA;visible.FILES=FILES
    visible.install(base,health);base.compose=compose
    base.compose('exec','-T','bot','node','--input-type=module','-e',visible.IDLE)
    backup=Path('/srv/zec-tip-bot/.update-backups')/('address-policy-'+uuid.uuid4().hex);backup.mkdir(mode=0o700)
    for name,content in old.items():(backup/name).write_bytes(content)
    stopped=False;changed=False
    try:
        base.compose('stop','-t','45','bot',quiet=False);stopped=True
        base.compose('run','--rm','--no-deps','bot','node','--input-type=module','-e',visible.IDLE)
        with closing(sqlite3.connect('file:'+str(DATABASE)+'?mode=ro',uri=True)) as db:
            if db.execute("SELECT COUNT(*) FROM welcome_grants WHERE phase NOT IN ('submitted','failed')").fetchone()[0]:
                raise ValueError('Wait for native welcome work to finish before repairing the adapter.')
        subprocess.run(['systemctl','stop',UNIT],check=True,timeout=45);changed=True
        if CONTROL.read_bytes()!=control:raise ValueError('Operator controls changed during the repair.')
        for name in old:original.replace(NATIVE/name,files['deploy/'+name])
        subprocess.run(['systemctl','start',UNIT],check=True,timeout=45)
        for attempt in range(30):
            try:
                value=health.read('/health')
                if value.get('receivingIndex')==4294967296 and value.get('transfersEnabled') is True:break
            except Exception:pass
            if attempt==29:raise ValueError('The fixed shielded address policy did not become ready.')
            time.sleep(2)
        base.compose('start','bot',quiet=False);stopped=False;base.wait_web()
    except BaseException:
        if changed:
            if not stopped:base.compose('stop','-t','45','bot',quiet=False);stopped=True
            subprocess.run(['systemctl','stop',UNIT],check=True,timeout=45)
            with closing(sqlite3.connect('file:'+str(DATABASE)+'?mode=ro',uri=True)) as db:
                active=db.execute('SELECT COUNT(*) FROM enrollment_bindings').fetchone()[0]
            if not active:
                for name,content in old.items():original.replace(NATIVE/name,content)
            subprocess.run(['systemctl','start',UNIT],check=True,timeout=45)
        raise
    finally:
        if stopped:base.compose('start','bot',quiet=False)
    print(json.dumps({'network':'testnet','fixedShieldedAddressPolicyInstalled':True,'existingReceivingAddressesChanged':False,
        'pendingNativeAccountPreserved':True,'accountsCreatedByRepair':0,'paymentsRequestedByRepair':0,
        'welcomeBudgetChanged':False,'workerConfigurationPreserved':True,'privateEnvironmentChanged':False},indent=2))
    print('Resume Create my Testnet wallet once on the pending third account. It reuses that account.')


if __name__=='__main__':
    try:main()
    except Exception as error:print('Address repair stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__));raise SystemExit(1)
