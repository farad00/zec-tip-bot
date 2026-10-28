#!/usr/bin/env python3
"""Install the held adapter and prepare its linked-account recovery backup."""
import os
from pathlib import Path
import stat
import subprocess
import sys


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    paths=[Path('/root/install-wallet-transfers-held.py'),Path('/root/prepare-linked-wallet-backup.py')]
    for path in paths:
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise ValueError('Expected the uploaded public root helpers.')
    for path in paths:subprocess.run(['/usr/bin/python3',str(path)],check=True)
    print('Held Testnet transfer adapter and linked-wallet backup prepared. Deposits, transfers and posting remain disabled.')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Testnet pilot preparation stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
