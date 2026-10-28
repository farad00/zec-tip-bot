#!/usr/bin/env python3
"""Install fee-only refusal handling and archive one verified unsigned held attempt."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
ARCHIVE=Path('/root/zec-fee-handling.zip')
ARCHIVE_SHA='e2d85998acbf9d957c5fcbb013fa18a381c2a18d9caa38d0f3f46ab4642503d5'
OLD_BROKER_SHA='345fc78a6004c60932192c7ce9e5d69e8afe59b9b79cd6b9425140465bbbd6f6'
FILES={'src/account.ts','src/tip-engine.ts','src/wallet-transfer-client.ts','src/private-tips.ts','src/x.ts',
       'test/wallet-transfers.test.ts','test/private-reply-context.test.ts','deploy/wallet-transfer-broker.py'}
NATIVE=Path('/opt/zec-tip-bot-chain/wallet-transfer-broker.py')
DB=Path('/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite')
ARCHIVE_APP='''import {DatabaseSync} from "node:sqlite";
const source=process.argv[1];if(!/^\\d{1,20}$/.test(source)||process.env.ZCASH_NETWORK!=="testnet")process.exit(1);
const db=new DatabaseSync("/app/data/bot.sqlite");try{db.exec("PRAGMA busy_timeout=5000; BEGIN IMMEDIATE");
const row=db.prepare("SELECT j.status,j.reason,s.proposal,s.signed,s.txid FROM tip_jobs j LEFT JOIN tip_spends s ON s.source=j.source WHERE j.source=? AND j.network='testnet'").get(source);
if(!row||row.proposal||row.signed||row.txid||!(row.status==='review'||(row.status==='failed'&&row.reason==='network_fee_limit')))throw Error('Unsafe hold');
db.prepare("UPDATE tip_jobs SET status='failed',reason='network_fee_limit',reserved_zat=0 WHERE source=? AND status='review'").run(source);
db.exec("COMMIT");console.log(JSON.stringify({unsignedAttemptArchived:true}));}catch{try{db.exec("ROLLBACK");}catch{}process.exitCode=1;}finally{db.close();}'''

def held_context(base,deposit,load):
    inspector=load(Path('/root/inspect-held-private-tip.py'),'fee_hold_inspector')
    planning=load(Path('/root/diagnose-held-tip-preparation.py'),'fee_hold_planning')
    state=planning.STATE.lstat()
    if state.st_uid!=0 or state.st_mode&0o077 or state.st_size>4_200_000:raise ValueError('Expected private persisted unsigned evidence.')
    evidence=json.loads(planning.STATE.read_text())
    if evidence.get('phase')!='created':raise ValueError('The unsigned fee evidence was not completed.')
    app=json.loads(base.compose('exec','-T','bot','node','--input-type=module','-e',inspector.READ).stdout)
    if (not isinstance(app,dict) or app.get('proposal') or app.get('signed') or app.get('txid')
            or not (app.get('status')=='review' or (app.get('status')=='failed' and app.get('reason')=='network_fee_limit'))):
        raise ValueError('The held attempt is not proven to be before signing; no records were changed.')
    key=hashlib.sha256(('zec-tip-bot:testnet:'+app['source']).encode()).hexdigest()
    if key!=evidence.get('requestKey'):raise ValueError('The latest tip differs from the inspected held request.')
    with closing(sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM transfer_jobs WHERE request_key=?',(key,)).fetchone()
    if not row or row['phase'] not in ('review','rejected') or row['pczt'] or row['terms'] or row['signed']:
        raise ValueError('Native preparation-only evidence did not match; the attempt was preserved.')
    request=json.loads(row['request']);module=load(NATIVE,'fee_current_native');rpc=module.NativeRpc()
    value=rpc.call('pczt_inspect',[evidence['pczt']]);fee=value.get('fee_zat')
    identity=next(r for r in rpc.call('z_listaccounts',[False]) if r.get('account_uuid')==request['account'])
    checks=planning.terms_report(value,request,identity,rpc.call('getblockchaininfo')['blocks'])
    required=('transactionVersionMatches','consensusBranchMatches','fullPrivacyRecorded','walletCreated',
              'sourceHintsMatch','transparentInputsAndOutputsAbsent','recipientAndAmountMatch')
    if any(checks.get(k) is not True for k in required) or checks.get('externalOutputCount')!=1 or checks.get('missingOutputValues')!=0:
        raise ValueError('Unsigned financial evidence did not match the held request.')
    if type(fee) is not int or not request['maxFeeZat']<fee<=module.MAX_FEE:raise ValueError('The held attempt is not the verified fee-only refusal.')
    if not deposit.read('/health').get('transfersEnabled'):raise ValueError('Wait for Testnet transfer readiness.')
    return app['source'],key

def archive_unsigned(source,key,base):
    # A rejected broker key can never be signed later. If the app update is
    # interrupted, it remains held until this same conditional update finishes.
    with closing(sqlite3.connect(DB,timeout=10)) as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT phase,pczt,terms,signed FROM transfer_jobs WHERE request_key=?',(key,)).fetchone()
        if not row or row[0] not in ('review','rejected') or any(row[1:]):raise ValueError('Native state changed; refusing archival.')
        db.execute("UPDATE transfer_jobs SET phase='rejected' WHERE request_key=? AND phase='review'",(key,));db.commit()
    result=base.compose('exec','-T','bot','node','--input-type=module','-e',ARCHIVE_APP,source)
    if json.loads(result.stdout)!={'unsignedAttemptArchived':True}:raise ValueError('Private app archival requires attention. Native rejection remains in place.')

def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    import importlib.util,stat
    path=Path('/root/update-visible-settings.py');info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected installed runtime-preserving updater.')
    spec=importlib.util.spec_from_file_location('fee_ui_base',path);visible=importlib.util.module_from_spec(spec);spec.loader.exec_module(visible)
    base=visible.load(Path('/root/update-dashboard.py'),'fee_original_base',visible.BASE_SHA)
    deposit=visible.load(Path('/root/enable-testnet-deposits.py'),'fee_readiness')
    base.ARCHIVE_SHA=ARCHIVE_SHA;base.FILES=FILES;files=base.payload(ARCHIVE.read_bytes())
    info=NATIVE.lstat();old=NATIVE.read_bytes()
    new=files['deploy/wallet-transfer-broker.py']
    if info.st_uid!=0 or info.st_mode&0o022 or hashlib.sha256(old).hexdigest() not in {OLD_BROKER_SHA,hashlib.sha256(new).hexdigest()}:
        raise ValueError('The installed native adapter differs from the verified source.')
    base.compose('exec','-T','bot','node','--input-type=module','-e',visible.IDLE)
    source,key=held_context(base,deposit,visible.load)
    changed=False
    try:
        # Stop only the broker; the native wallet and chain stay running.
        subprocess.run(['systemctl','stop','zec-tip-bot-wallet-accounts.service'],check=True)
        changed=True
        archive_unsigned(source,key,base)
        NATIVE.write_bytes(new);NATIVE.chmod(0o644)
        subprocess.run(['systemctl','start','zec-tip-bot-wallet-accounts.service'],check=True)
        import time
        for attempt in range(20):
            try:
                if deposit.read('/health').get('transfersEnabled') is True:break
            except Exception:pass
            if attempt==19:raise ValueError('Updated broker readiness requires attention.')
            time.sleep(1)
        visible.ARCHIVE=ARCHIVE;visible.ARCHIVE_SHA=ARCHIVE_SHA;visible.FILES=FILES
        report=visible.install(base,deposit)
    except BaseException:
        if changed:
            NATIVE.write_bytes(old);NATIVE.chmod(0o644)
            subprocess.run(['systemctl','restart','zec-tip-bot-wallet-accounts.service'],check=True)
        # Do not undo the definitive rejection or rewind the mention cursor.
        raise
    print(json.dumps({'network':'testnet','feeLimitHandlingInstalled':True,'unsignedHeldAttemptArchivedAsNotSent':True,
        'savedUserLimitsChanged':False,'workerConfigurationPreserved':report['workerConfigurationPreserved'],
        'transfersEnabled':report['transfersEnabled'],'signingOrBroadcastingRequestedByUpdater':False,
        'newPilotEnrollmentHeld':True},indent=2))
    print('Reload your settings and explicitly save a maximum fee of 0.001 TAZ before one fresh tip command.')
if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Fee update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
