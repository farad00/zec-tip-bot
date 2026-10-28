#!/usr/bin/env python3
"""Install verified handling of X's hidden reply-context prefix. Never rewind commands."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
ARCHIVE_SHA='aafe08f1de2570b227d31b26e9cffe15ab43c13e49244aac5099b51b6afd73df'
HELPER_SHA='2914c6a38df74497883ab7adb3300851999523cc57badc7e98df1cb1c47ba4bf'
FILES=('Dockerfile', 'src/x.ts', 'src/private-tips.ts', 'test/private-reply-context.test.ts')
def main():
 if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
 path=Path('/root/update-visible-settings.py');info=path.lstat()
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or hashlib.sha256(path.read_bytes()).hexdigest()!=HELPER_SHA:raise ValueError('Expected the verified runtime-preserving website updater.')
 spec=importlib.util.spec_from_file_location('reply_update_base',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 module.ARCHIVE=Path('/root/zec-private-reply-fix.zip');module.ARCHIVE_SHA=ARCHIVE_SHA;module.FILES=set(FILES)
 base=module.load(Path('/root/update-dashboard.py'),'reply_original_base',module.BASE_SHA)
 deposit=module.load(Path('/root/enable-testnet-deposits.py'),'reply_health')
 report=module.install(base,deposit)
 print(json.dumps({'privateReplyParsingFixed':True,'network':'testnet','workerConfigurationPreserved':report['workerConfigurationPreserved'],
  'transfersEnabled':report['transfersEnabled'],'automaticXWorkerEnabled':report['automaticXWorkerEnabled'],
  'oldCommandsReplayed':False,'paymentsRequestedByInstaller':False},indent=2))
 print('Post one new tip comment after installation. The earlier skipped command was not replayed.')
if __name__=='__main__':
 try:main()
 except Exception as error:
  print('Private reply update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
  raise SystemExit(1)
