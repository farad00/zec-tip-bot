#!/usr/bin/env python3
"""Install final copy and disable only the operator's local X testing guard."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
ARCHIVE=Path('/root/zec-submission-settings.zip')
ARCHIVE_SHA='bf367192412cbfed0b8f3df585d6f352da4855c6289f4afc809a8992f9a8705f'
FILES={'Dockerfile','src/config.ts','test/x-usage-guard.test.ts','src/account.ts','src/account-ui.ts'}
ENV=Path('/srv/zec-tip-bot/.env')
CHECK='''import {loadConfig} from "./dist/src/config.js";const c=loadConfig();if(c.usageGuard!==false||c.budgetMills!==Number.MAX_SAFE_INTEGER||c.maxDailyReplies!==Number.MAX_SAFE_INTEGER||!c.privateTips||c.network!=="testnet"||!c.hostedWallets||!c.posting||c.pollSeconds<30)process.exit(1);console.log(JSON.stringify({localXTestingGuardEnabled:c.usageGuard}));'''

def configured(raw):
 text=raw.decode('utf-8');pattern=r'(?m)^X_USAGE_GUARD_ENABLED=.*$'
 if len(re.findall(pattern,text))>1:raise ValueError('Duplicate X guard settings were preserved for review.')
 line='X_USAGE_GUARD_ENABLED=false'
 return (re.sub(pattern,line,text) if re.search(pattern,text) else text.rstrip()+'\n'+line+'\n').encode('utf-8')

def replace_env(raw):
 path=ENV.with_name('.env-submission-'+uuid.uuid4().hex)
 try:
  with path.open('xb') as file:os.chmod(path,0o600);file.write(raw);file.flush();os.fsync(file.fileno())
  os.replace(path,ENV)
 finally:
  if path.exists():path.unlink()

def main():
 if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the VPS root terminal.')
 path=Path('/root/update-visible-settings.py');info=path.lstat()
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected the protected runtime-preserving updater.')
 spec=importlib.util.spec_from_file_location('submission_ui_base',path);visible=importlib.util.module_from_spec(spec);spec.loader.exec_module(visible)
 base=visible.load(Path('/root/update-dashboard.py'),'submission_original_base',visible.BASE_SHA)
 deposit=visible.load(Path('/root/enable-testnet-deposits.py'),'submission_health')
 info=ENV.lstat()
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Expected the private existing environment file.')
 before=ENV.read_bytes();after=configured(before)
 visible.ARCHIVE=ARCHIVE;visible.ARCHIVE_SHA=ARCHIVE_SHA;visible.FILES=FILES
 report=visible.install(base,deposit)
 if ENV.read_bytes()!=before:raise ValueError('Environment changed during the build; the existing file was preserved.')
 try:
  replace_env(after)
  base.compose('up','-d','--no-deps','--force-recreate','bot',quiet=False)
  base.wait_web();base.compose('exec','-T','bot','node','--input-type=module','-e',CHECK)
 except BaseException:
  replace_env(before);base.compose('up','-d','--no-deps','--force-recreate','bot',quiet=False)
  raise
 print(json.dumps({'network':'testnet','requestedCopyRemoved':True,'localXTestingGuardEnabled':False,
    'xConsoleBillingControlsStillApply':True,'walletSpendingCapsChanged':False,'signupPolicyChanged':False,
    'workerConfigurationPreserved':report['workerConfigurationPreserved'],'transfersEnabled':report['transfersEnabled'],
    'credentialsPrinted':False},indent=2))
 print('Refresh the live wallet. Operator X testing limits are disabled; wallet financial limits are unchanged.')

if __name__=='__main__':
 try:main()
 except Exception as error:
  print('Submission settings update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
  raise SystemExit(1)
