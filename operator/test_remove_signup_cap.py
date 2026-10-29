from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location('signup_count_update',ROOT/'remove-signup-cap.py')
update=importlib.util.module_from_spec(spec);spec.loader.exec_module(update)


class SignupCountUpdateTests(unittest.TestCase):
    def controls(self):return {'network':'testnet','enabled':True,'depositsEnabled':True,'bindingBackupVerified':True,'minimumConfirmations':3,
        'enrollment':{'enabled':True,'welcome':{'maximumGrants':12,'amountZat':300000,'maxFeeZat':100000}}}

    def test_existing_welcome_authorization_is_read_without_being_changed(self):
        before=self.controls();raw=json.dumps(before).encode()
        self.assertEqual(update.policy(raw),before)
        for name,value in [('maximumGrants',13),('amountZat',300001),('maxFeeZat',100001)]:
            changed=self.controls();changed['enrollment']['welcome'][name]=value
            with self.assertRaises(ValueError):update.policy(json.dumps(changed).encode())
        changed=self.controls();changed['network']='mainnet'
        with self.assertRaises(ValueError):update.policy(json.dumps(changed).encode())

    def test_record_snapshot_detects_changes_without_modifying_the_database(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            database=Path(temp)/'records.sqlite'
            with closing(sqlite3.connect(database)) as db:
                for name in ('account_intents','enrollment_bindings','welcome_grants','transfer_jobs'):
                    db.execute('CREATE TABLE '+name+'(value TEXT)')
                db.execute("INSERT INTO welcome_grants VALUES ('synthetic grant')");db.commit()
            with patch.object(update,'DATABASE',database):
                before=update.records();self.assertEqual(update.records(),before);self.assertEqual(before[1],1)
                with closing(sqlite3.connect(database)) as db:db.execute("INSERT INTO account_intents VALUES ('synthetic account')");db.commit()
                self.assertNotEqual(update.records()[0],before[0])

    def exercise(self,*,failure=None):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            app=Path(temp);(app/'.update-backups').mkdir();native=app/'native';native.mkdir();control=app/'control.json'
            control_raw=json.dumps(self.controls()).encode();control.write_bytes(control_raw)
            old={name:('OLD '+name).encode() for name in update.OLD}
            for name,raw in old.items():(native/name).write_bytes(raw)
            files={'deploy/'+name:('NEW '+name).encode() for name in old}
            calls=[];failed=False
            def compose(*args,**kwargs):
                calls.append(args)
                if failure=='busy' and args[0]=='run':raise ValueError('Synthetic pending work')
                return SimpleNamespace(stdout='')
            def replace(path,raw,previous=None):
                nonlocal failed
                if failure=='partial' and path.name=='wallet-transfer-broker.py' and not failed:failed=True;raise OSError('Synthetic write failure')
                path.write_bytes(raw)
            base=SimpleNamespace(compose=compose)
            health=SimpleNamespace(read=Mock(return_value={'network':'testnet','accountCountCapEnabled':failure=='health',
                'transfersEnabled':True,'accountCreationReady':True,'accountAdmissionRequired':True}))
            atomic=SimpleNamespace(replace=replace,protected=Mock())
            idle=SimpleNamespace(IDLE='synthetic read-only queue query',native_idle=Mock())
            snapshots=Mock(side_effect=[('original',1),('changed',1)] if failure=='records' else None,return_value=('original',1))
            with patch.multiple(update,APP=app,NATIVE=native,CONTROL=control),patch.object(update,'records',snapshots),patch.object(update.subprocess,'run') as services,patch.object(update.time,'sleep'):
                if failure:
                    with self.assertRaises((ValueError,OSError)):update.activate(files,old,control_raw,base,health,atomic,idle)
                else:self.assertEqual(update.activate(files,old,control_raw,base,health,atomic,idle),1)
            self.assertEqual(control.read_bytes(),control_raw)
            self.assertIn(('start','bot'),calls)
            for name,raw in old.items():self.assertEqual((native/name).read_bytes(),raw if failure else files['deploy/'+name])
            self.assertTrue(all(call.args[0] in (['systemctl','start',update.UNIT],['systemctl','stop',update.UNIT]) for call in services.call_args_list))
            if failure=='busy':services.assert_not_called();health.read.assert_not_called()
            if not failure:self.assertEqual(len(services.call_args_list),2)

    def test_success_preserves_control_and_records_without_restarting_wallet_or_node(self):self.exercise()
    def test_busy_queue_prevents_native_changes(self):self.exercise(failure='busy')
    def test_health_failure_restores_both_native_sources(self):self.exercise(failure='health')
    def test_partial_file_write_rolls_back_both_sources(self):self.exercise(failure='partial')
    def test_unexpected_record_changes_never_trigger_record_deletion(self):self.exercise(failure='records')


if __name__=='__main__':unittest.main()
