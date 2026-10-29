#!/usr/bin/env python3
"""Apply the shorter wallet introduction without changing wallet behavior."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

HELPER_SHA='361110a0c980ffcd969d0138554304a01dc5e2cef7c393400a1fc841c83073bc'
ARCHIVE_SHA='a8651fd77f16c82ab4985baab1ed1171da3aba273368992bcc078217053c4ee2'


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    path=Path('/root/update-zectip-logo.py');info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid!=0 or info.st_mode&0o022 or hashlib.sha256(path.read_bytes()).hexdigest()!=HELPER_SHA:
        raise ValueError('Expected the verified, installed logo and signup updater.')
    spec=importlib.util.spec_from_file_location('wallet_intro_base',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.ARCHIVE=Path('/root/zec-wallet-intro.zip');module.ARCHIVE_SHA=ARCHIVE_SHA
    module.FILES={'src/account.ts'}
    module.PROBE+='\nif(!signup.includes("Create a Testnet wallet for this verified X account.")||signup.includes("Create a dedicated hosted Testnet wallet"))process.exit(1);\n'
    module.main()
    print(json.dumps({'walletIntroductionUpdated':True,'runningCopyVerified':True,'walletSettingsChanged':False,
        'domainOrLogoChanged':False,'paymentsRequestedByInstaller':False},indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Wallet introduction update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
