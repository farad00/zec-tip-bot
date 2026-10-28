#!/usr/bin/env python3
"""Collect only public sources from the pinned build image. Never open a live wallet."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

IMAGE='zec-nu7-finish:8c5a93b-r5'
OUTPUT=Path('/root/zec-wallet-transfer-sources.json')
REVISION='8c5a93ba9d68ad78d199c3624413c0139781b837'
NODE_REVISION='e8db87eb69254e0d4eedd058eadd48bdfa09065a'
COLLECT=r'''import json,os,pathlib,subprocess
root=pathlib.Path('/work/zallet')
revision=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],check=True,text=True,capture_output=True).stdout.strip()
if revision!='8c5a93ba9d68ad78d199c3624413c0139781b837':raise ValueError('Unexpected public wallet revision')
paths=[]
for folder in ('zallet-core/src/components/json_rpc','zallet-core/src/components/sync','backends/zebra/src'):
    paths.extend((root/folder).rglob('*.rs'))
paths.extend(root/name for name in ('zallet-core/src/components/json_rpc.rs','zallet-core/src/components/sync.rs','zallet-core/src/config.rs'))
files={}
for path in sorted(set(paths)):
    if not path.exists():continue
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):raise ValueError('Unexpected public source path')
    if path.stat().st_size>1000000:raise ValueError('Unexpected public source size')
    files[str(path.relative_to(root))]=path.read_text()
node=pathlib.Path('/work/zebra')
node_revision=subprocess.run(['git','-C',str(node),'rev-parse','HEAD'],check=True,text=True,capture_output=True).stdout.strip()
if node_revision!='e8db87eb69254e0d4eedd058eadd48bdfa09065a':raise ValueError('Unexpected public node revision')
path=node/'zebra-rpc/src/methods.rs'
if path.is_symlink() or not path.is_file() or path.stat().st_size>1000000:raise ValueError('Unexpected public node source')
files['zebra/zebra-rpc/src/methods.rs']=path.read_text()
libraries=set()
for cargo in (pathlib.Path(os.environ.get('CARGO_HOME','/usr/local/cargo')),pathlib.Path('/home/builder/.cargo')):
    libraries.update((cargo/'registry/src').glob('*/zcash_client_backend-0.25.0'))
if len(libraries)!=1:raise ValueError('Unexpected public backend dependency')
library=libraries.pop()
for relative in ('src/data_api/wallet.rs','src/sync.rs'):
    path=library/relative
    if path.exists():
        if path.is_symlink() or path.stat().st_size>1000000:raise ValueError('Unexpected public library source')
        files['zcash_client_backend-0.25.0/'+relative]=path.read_text()
if sum(len(value) for value in files.values())>8000000:raise ValueError('Public source bundle too large')
json.dump({'walletRevision':revision,'nodeRevision':node_revision,'files':files},__import__('sys').stdout)
'''


def collect():
    command=['docker','run','--rm','--network','none','--cap-drop','ALL',
             '--security-opt','no-new-privileges','--memory','256m','--pids-limit','64',
             '--entrypoint','python3',IMAGE,'-c',COLLECT]
    result=subprocess.run(command,check=True,text=True,capture_output=True,timeout=60)
    if len(result.stdout)>10000000:raise ValueError('Public source result too large.')
    document=json.loads(result.stdout)
    if (document.get('walletRevision')!=REVISION or document.get('nodeRevision')!=NODE_REVISION or not isinstance(document.get('files'),dict)
            or len(document['files'])<10 or any(not isinstance(value,str) for value in document['files'].values())):
        raise ValueError('Unexpected public source bundle.')
    content=json.dumps(document).encode()
    if OUTPUT.is_symlink():raise ValueError('Public source output must not be a symbolic link.')
    if OUTPUT.exists():
        if OUTPUT.read_bytes()!=content:raise ValueError('An existing different public source bundle was preserved.')
    else:
        with OUTPUT.open('xb') as file:file.write(content)
        OUTPUT.chmod(0o600)
    return {'publicSourceFile':str(OUTPUT),'sourceFiles':len(document['files']),
            'publicSourceSha256':hashlib.sha256(content).hexdigest(),
            'walletOperations':False,'serviceChanges':False,'createsKeysOrAccounts':False}


if __name__=='__main__':
    try:
        if sys.platform!='linux' or os.geteuid()!=0 or Path('/etc/hostname').read_text().strip()!='vmi3703861':
            raise ValueError('Run in the existing VPS root terminal.')
        print(json.dumps(collect(),indent=2))
    except subprocess.CalledProcessError as error:
        # Only the isolated public build image is involved; no native daemon output.
        print(json.dumps({'publicSourceCollection':'failed','exitCode':error.returncode,
                          'publicBuildDiagnostic':(error.stderr or '')[-2500:]}))
        sys.exit(1)
    except Exception as error:
        print('Public source collection stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        sys.exit(1)
