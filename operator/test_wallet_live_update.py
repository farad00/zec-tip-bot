import hashlib
import importlib.util
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location('wallet_live_release',ROOT/'update-wallet-live.py')
update=importlib.util.module_from_spec(spec);spec.loader.exec_module(update)


def archive(name):
    path=ROOT/name
    return path if path.exists() else ROOT/'releases'/name


class WalletLiveUpdateTests(unittest.TestCase):
    def test_exact_source_archive_rejects_wrong_hash_and_keeps_credentials_out(self):
        raw=archive('zec-wallet-live.zip').read_bytes()
        files=update.payload(raw)
        self.assertEqual(set(files),update.FILES)
        self.assertNotIn('.env',files)
        with self.assertRaises(ValueError):update.payload(raw+b'changed')
        self.assertIn(b'insufficient_funds',files['deploy/wallet-transfer-broker.py'])

    def test_all_operator_dependencies_are_the_unchanged_reviewed_helpers(self):
        for name,digest in update.PINS.items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),digest)

    def test_proving_signing_broadcast_and_spend_validation_are_unchanged(self):
        with zipfile.ZipFile(archive('zec-open-signups.zip')) as z:
            old=z.read('deploy/wallet-transfer-broker.py')
        self.assertEqual(hashlib.sha256(old).hexdigest(),update.OLD_TRANSFER_SHA)
        new=update.payload(archive('zec-wallet-live.zip').read_bytes())['deploy/wallet-transfer-broker.py']
        old=old.decode().replace('\r\n','\n');new=new.decode().replace('\r\n','\n')
        self.assertEqual(old[old.index('    def sign('):old.index('def read_account_balance')],new[new.index('    def sign('):new.index('def read_account_balance')])
        self.assertEqual(old[old.index('def inspect_terms('):old.index('class Transfers')],new[new.index('def inspect_terms('):new.index('class Transfers')])

    def test_archive_broker_tests_run_on_synthetic_state(self):
        files=update.payload(archive('zec-wallet-live.zip').read_bytes())
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            folder=Path(temp)
            for name,raw in files.items():
                if name.startswith(('operator/','deploy/')):
                    path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            result=subprocess.run([sys.executable,'-m','unittest','discover','-s','operator','-p','test_wallet_*broker.py'],cwd=folder,capture_output=True,text=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('Ran 52 tests',result.stderr)

    def test_installer_probe_checks_private_http_response_without_native_or_x_calls(self):
        app=ROOT/'zec-tip-bot'
        if not app.exists():app=ROOT.parent
        node=r'C:\Program Files\nodejs\node.exe' if sys.platform=='win32' else shutil.which('node')
        source=update.PROBE.replace('const c=loadConfig();','''const c=loadConfig();
const state=new State(":memory:"),server=paymentServer(c,state);await listen(server,"127.0.0.1",0);const port=server.address().port;''').replace('port:3001','port')
        source='import {State} from "./dist/src/state.js";import {paymentServer,listen} from "./dist/src/web.js";\n'+source+'\nawait new Promise(resolve=>server.close(resolve));state.close();'
        result=subprocess.run([node,'--input-type=module','-e',source],cwd=app,env={
            'SystemRoot':os.environ.get('SystemRoot',r'C:\Windows'),'PRIVATE_TIPPING_ENABLED':'true','ZCASH_NETWORK':'testnet',
            'PUBLIC_BASE_URL':'https://tips.example.org'},capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,'')

    def test_only_the_operator_linux_terminal_can_install(self):
        with patch.object(update.sys,'platform','win32'),self.assertRaises(ValueError):update.main()


if __name__=='__main__':unittest.main()
