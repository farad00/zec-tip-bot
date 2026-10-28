#!/usr/bin/env python3
"""Apply the minimal UI through the already-tested presentation-only installer."""
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import sys
ARCHIVE_SHA='a767af03d7393c95ad5cb76f0b5b8bb47a55c20aaf5aa35c99b5453c4dd8ec9f'
BASE_SHA='e4610816c179bc468e7023a26c537431ba3c177a5c9d83fd6aac6c7b8adf89e4'
def main():
 if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
 path=Path('/root/update-dashboard.py');info=path.lstat()
 if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or hashlib.sha256(path.read_bytes()).hexdigest()!=BASE_SHA:
  raise ValueError('The previously installed dashboard helper differs from the verified version.')
 spec=importlib.util.spec_from_file_location('minimal_dashboard_base',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 module.ARCHIVE=Path('/root/zec-minimal-dashboard.zip');module.ARCHIVE_SHA=ARCHIVE_SHA
 module.main()
if __name__=='__main__':
 try:main()
 except Exception as error:
  print('Minimal dashboard update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__),file=sys.stderr)
  raise SystemExit(1)
