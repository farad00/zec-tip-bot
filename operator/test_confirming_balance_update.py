from contextlib import closing
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location('confirming_update',ROOT/'update-confirming-balance.py')
update=importlib.util.module_from_spec(spec);spec.loader.exec_module(update)
A='11111111-1111-4111-8111-111111111111'


class ConfirmingUpdateTests(unittest.TestCase):
    def test_policy_changes_only_the_testnet_confirmation_setting(self):
        original={'network':'testnet','enabled':True,'depositsEnabled':True,'bindingBackupVerified':True,
                  'enrollment':{'welcome':{'maximumGrants':12,'amountZat':300000,'maxFeeZat':100000}}}
        after=update.configured(original)
        self.assertNotIn('minimumConfirmations',original);self.assertEqual(after['minimumConfirmations'],3)
        self.assertEqual({k:v for k,v in after.items() if k!='minimumConfirmations'},original)
        for change in ({'network':'mainnet'},{'enabled':False},{'minimumConfirmations':0},{'minimumConfirmations':True},{'bindingBackupVerified':False}):
            with self.assertRaises(ValueError):update.configured({**original,**change})

    def exercise(self,fail=False,busy=False):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            folder=Path(temp);native=folder/'native';native.mkdir();control=folder/'control';dbpath=folder/'broker.db'
            backup=folder/'backup';backup.mkdir();old_control=json.dumps({'network':'testnet','minimumConfirmations':10}).encode();control.write_bytes(old_control)
            old={'wallet-transfer-broker.py':b'OLD TRANSFER','wallet-enrollment.py':b'OLD ENROLLMENT'}
            for name,raw in old.items():(native/name).write_bytes(raw)
            files={'deploy/'+n:b'NEW '+raw for n,raw in old.items()}
            with closing(sqlite3.connect(dbpath)) as db:
                db.executescript('CREATE TABLE transfer_jobs(phase TEXT);CREATE TABLE welcome_grants(phase TEXT);CREATE TABLE enrollment_bindings(account TEXT,phase TEXT);')
                db.execute("INSERT INTO enrollment_bindings VALUES (?,'active')",(A,))
                if busy:db.execute("INSERT INTO welcome_grants VALUES ('queued')")
                db.commit()
            desired={'network':'testnet','minimumConfirmations':3,'enrollment':{'baselineAccounts':[]}}
            def read(path):
                if path=='/health':return {'minimumConfirmations':3,'transfersEnabled':True}
                if path.startswith('/welcome/'):return {'state':'confirmed'}
                if fail:raise ValueError('Probe failed')
                return {'network':'testnet','account':A,'minimumConfirmations':3,'confirmedSpendableZat':0,'pendingZat':190000,'uneconomicZat':0}
            base=SimpleNamespace(compose=Mock(),wait_web=Mock())
            atomic=SimpleNamespace(replace=lambda path,raw,info=None:path.write_bytes(raw))
            original_mkdir=Path.mkdir
            def mkdir(path,*args,**kwargs):
                if str(path).replace('\\','/').startswith('/srv/zec-tip-bot/.update-backups/'):
                    return None
                return original_mkdir(path,*args,**kwargs)
            # Redirect the fixed backup path, preserving all source/config writes
            # inside this synthetic test directory.
            real_path=update.Path
            def paths(value):
                if value=='/srv/zec-tip-bot/.update-backups':return backup
                return real_path(value)
            with patch.object(update,'NATIVE',native),patch.object(update,'CONTROL',control),patch.object(update,'DATABASE',dbpath),patch.object(update,'Path',side_effect=paths),patch.object(update.subprocess,'run') as services:
                if fail or busy:
                    with self.assertRaises(ValueError):update.activate(files,old,old_control,SimpleNamespace(),desired,base,SimpleNamespace(read=read),atomic)
                else:self.assertEqual(update.activate(files,old,old_control,SimpleNamespace(),desired,base,SimpleNamespace(read=read),atomic),(1,1))
            if busy:
                services.assert_not_called();self.assertEqual(control.read_bytes(),old_control)
            elif fail:
                self.assertEqual(control.read_bytes(),old_control)
                for name,raw in old.items():self.assertEqual((native/name).read_bytes(),raw)
                self.assertIn(unittest.mock.call('start','bot',quiet=False),base.compose.call_args_list)
            else:
                self.assertEqual(json.loads(control.read_bytes())['minimumConfirmations'],3)
            self.assertFalse(any('zec-tip-bot-wallet-testnet.service' in str(c) or 'zec-tip-bot-chain-testnet.service' in str(c) for c in services.call_args_list))

    def test_success_probes_real_categories_without_restarting_wallet_or_node(self):self.exercise()
    def test_failed_read_probe_rolls_back_source_and_policy_preserving_databases(self):self.exercise(fail=True)
    def test_unfinished_welcome_payment_stops_before_any_service_change(self):self.exercise(busy=True)


if __name__=='__main__':unittest.main()
