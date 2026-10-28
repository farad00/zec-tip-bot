#!/usr/bin/env python3
"""Install owner-scoped read-only receipt history without changing signing or controls."""
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time
ARCHIVE=Path('/root/zec-received-history.zip')
ARCHIVE_SHA='90b8b5ac00d0ea4a0d49e27b5b85c2524e807f654b9e94063939365a716b2abc'
OLD_BROKER_SHA='ce609243d463532850a9e67dd372e34fce0ab2dfa837b5c580e4a87dce2bb8c9'
NATIVE=Path('/opt/zec-tip-bot-chain/wallet-transfer-broker.py')
FILES={'src/account.ts','src/wallet-accounts.ts','src/wallet-account-client.ts','deploy/wallet-transfer-broker.py',
       'test/account-ui.test.ts','test/wallet-account-client.test.ts'}

def main():
 if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the VPS root terminal.')
 path=Path('/root/update-visible-settings.py');info=path.lstat()
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected the protected runtime-preserving updater.')
 spec=importlib.util.spec_from_file_location('receipts_ui_loader',path);visible=importlib.util.module_from_spec(spec);spec.loader.exec_module(visible)
 base=visible.load(Path('/root/update-dashboard.py'),'receipts_base',visible.BASE_SHA)
 deposit=visible.load(Path('/root/enable-testnet-deposits.py'),'receipts_health')
 base.ARCHIVE_SHA=ARCHIVE_SHA;base.FILES=FILES;files=base.payload(ARCHIVE.read_bytes())
 info=NATIVE.lstat();old=NATIVE.read_bytes();new=files['deploy/wallet-transfer-broker.py']
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or hashlib.sha256(old).hexdigest() not in {OLD_BROKER_SHA,hashlib.sha256(new).hexdigest()}:
  raise ValueError('The native adapter differs from the verified installed source.')
 base.compose('exec','-T','bot','node','--input-type=module','-e',visible.IDLE)
 with closing(sqlite3.connect('file:/var/lib/zec-tip-bot-wallet-broker/accounts.sqlite?mode=ro',uri=True)) as db:
  accounts=[row[0] for row in db.execute("SELECT account FROM account_intents WHERE phase='created' ORDER BY account")]
 if not accounts or len(accounts)>100:raise ValueError('Expected the backed-up pilot accounts.')
 before=deposit.read('/health')
 if before.get('network')!='testnet' or before.get('transfersEnabled') is not True:raise ValueError('Wait for normal Testnet readiness.')
 changed=False
 try:
  subprocess.run(['systemctl','stop','zec-tip-bot-wallet-accounts.service'],check=True);changed=True
  NATIVE.write_bytes(new);NATIVE.chmod(0o644)
  subprocess.run(['systemctl','start','zec-tip-bot-wallet-accounts.service'],check=True)
  for attempt in range(20):
   try:
    if deposit.read('/health').get('transfersEnabled') is True:break
   except Exception:pass
   if attempt==19:raise ValueError('Updated read-only history service did not become ready.')
   time.sleep(1)
  count=0
  for account in accounts:
   result=deposit.read('/receipts/'+account)
   if result.get('network')!='testnet' or result.get('account')!=account or not isinstance(result.get('receipts'),list) or len(result['receipts'])>30:
    raise ValueError('Owner-scoped native receipt read did not pass.')
   count+=len(result['receipts'])
  visible.ARCHIVE=ARCHIVE;visible.ARCHIVE_SHA=ARCHIVE_SHA;visible.FILES=FILES
  report=visible.install(base,deposit)
 except BaseException:
  if changed:
   NATIVE.write_bytes(old);NATIVE.chmod(0o644)
   subprocess.run(['systemctl','restart','zec-tip-bot-wallet-accounts.service'],check=True)
  raise
 print(json.dumps({'network':'testnet','privateReceiptHistoryInstalled':True,'privateAccountsChecked':len(accounts),
    'receiptRowsChecked':count,'signingOrBroadcastingRequested':False,'savedSettingsChanged':False,
    'workerConfigurationPreserved':report['workerConfigurationPreserved'],'transfersEnabled':report['transfersEnabled'],
    'addressesAmountsMemosOrTransactionIdsPrinted':False},indent=2))
 print('Refresh the live wallet. External deposits now have private Received or Confirming activity entries.')

if __name__=='__main__':
 try:main()
 except Exception as error:
  print('Receipt update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
  raise SystemExit(1)
