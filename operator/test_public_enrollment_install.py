from contextlib import closing
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import zipfile

ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location('public_enrollment_install',ROOT/'install-public-enrollment.py')
install=importlib.util.module_from_spec(spec);spec.loader.exec_module(install)
A='11111111-1111-4111-8111-111111111111';B='22222222-2222-4222-8222-222222222222'


class InstallationTests(unittest.TestCase):
    def controls(self):return {'network':'testnet','enabled':True,'depositsEnabled':True,'bindingBackupVerified':True,
        'accountsSha256':hashlib.sha256('\n'.join([A,B]).encode()).hexdigest()}

    def test_only_explicit_fixed_testnet_welcome_budget_is_configured(self):
        before=self.controls();after=install.make_control(before,[B,A],A,'age1'+'q'*58)
        self.assertNotIn('enrollment',before);self.assertEqual(after['accountsSha256'],before['accountsSha256'])
        grant=after['enrollment']['welcome'];self.assertEqual(grant['maximumGrants']*(grant['amountZat']+grant['maxFeeZat']),4_800_000)
        for old,accounts,sponsor in [({**before,'network':'mainnet'},[A,B],A),({**before,'bindingBackupVerified':False},[A,B],A),
                                     (before,[A],A),(before,[A,B],'other')]:
            with self.assertRaises(ValueError):install.make_control(old,accounts,sponsor,'age1'+'q'*58)

    def test_release_hash_and_exact_allowlist_are_required(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as archive:
            for name in install.WEB_FILES|install.TEST_FILES:archive.writestr(name,b'PUBLIC SYNTHETIC SOURCE')
        raw=out.getvalue()
        with patch.object(install,'ARCHIVE_SHA',hashlib.sha256(raw).hexdigest()):self.assertEqual(set(install.payload(raw)),install.WEB_FILES|install.TEST_FILES)
        with self.assertRaises(ValueError):install.payload(raw)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as archive:archive.writestr('.env','SHOULD NOT BE ACCEPTED')
        raw=out.getvalue()
        with patch.object(install,'ARCHIVE_SHA',hashlib.sha256(raw).hexdigest()),self.assertRaises(ValueError):install.payload(raw)

    def exercise(self,ready=True,new_binding=False,busy_after_stop=False):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            folder=Path(temp);native=folder/'native';native.mkdir();data=folder/'data';data.mkdir()
            app=folder/'app';(app/'.update-backups').mkdir(parents=True)
            control=folder/'control.json';old=json.dumps(self.controls()).encode();control.write_bytes(old)
            originals={n:('OLD '+n).encode() for n in install.OLD}
            for n,v in originals.items():(native/n).write_bytes(v)
            files={'deploy/'+n:('NEW '+n).encode() for n in (*install.OLD,'wallet-enrollment.py')}
            with closing(sqlite3.connect(data/'accounts.sqlite')) as db:
                db.execute('CREATE TABLE enrollment_bindings(account TEXT)');db.execute('CREATE TABLE welcome_grants(account TEXT)')
                if new_binding:db.execute('INSERT INTO enrollment_bindings VALUES (?)',(B,));db.commit()
            calls=[]
            def compose(*args,**kwargs):
                calls.append(args)
                if busy_after_stop and args[0]=='run':raise ValueError('A new job appeared')
                return SimpleNamespace(stdout='')
            base=SimpleNamespace(compose=compose,wait_web=Mock())
            health=SimpleNamespace(read=lambda _: {'accountCreationReady':ready,'transfersEnabled':ready,'accountAdmissionRequired':True})
            desired=install.make_control(self.controls(),[A,B],A,'age1'+'q'*58)
            def replace(path,raw,previous=None):path.write_bytes(raw)
            with patch.object(install,'APP',app),patch.object(install,'CONTROL',control),patch.object(install,'NATIVE',native),patch.object(install,'DATA',data),patch.object(install,'replace',side_effect=replace),patch.object(install.subprocess,'run') as services,patch.object(install.time,'sleep'):
                if not ready or busy_after_stop:
                    with self.assertRaises(ValueError):install.activate(files,desired,base,health,SimpleNamespace(IDLE='READ ONLY'),originals,old,SimpleNamespace())
                else:install.activate(files,desired,base,health,SimpleNamespace(IDLE='READ ONLY'),originals,old,SimpleNamespace())
            self.assertIn(('start','bot'),calls)
            if busy_after_stop:
                services.assert_not_called();self.assertEqual(control.read_bytes(),old)
            elif not ready and not new_binding:
                self.assertEqual(control.read_bytes(),old)
                for name,raw in originals.items():self.assertEqual((native/name).read_bytes(),raw)
                self.assertFalse((native/'wallet-enrollment.py').exists())
            elif not ready:
                self.assertFalse(json.loads(control.read_bytes())['enrollment']['enabled'])
                self.assertFalse(json.loads(control.read_bytes())['enrollment']['welcome']['enabled'])
                self.assertTrue((native/'wallet-enrollment.py').exists())
            else:
                self.assertTrue(json.loads(control.read_bytes())['enrollment']['enabled']);base.wait_web.assert_called_once()
            self.assertFalse(any('zallet' in str(call) or 'zec-tip-bot-wallet-testnet.service' in str(call) for call in services.call_args_list))

    def test_success_preserves_wallet_node_and_workers(self):self.exercise()
    def test_failed_activation_rolls_back_before_any_new_signup(self):self.exercise(ready=False)
    def test_queue_race_before_restart_preserves_running_native_backend(self):self.exercise(busy_after_stop=True)
    def test_failed_activation_after_real_signup_holds_admission_instead_of_erasing_coverage(self):self.exercise(ready=False,new_binding=True)


if __name__=='__main__':unittest.main()
