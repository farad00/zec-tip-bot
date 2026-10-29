import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('transfer_fixtures',Path(__file__).with_name('test_wallet_transfer_broker.py'))
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
broker=fixtures.broker;enrollment=broker.enrollment_module
A=fixtures.A;B='22222222-2222-4222-8222-222222222222';C='33333333-3333-4333-8333-333333333333'
LABEL='tip-'+'2'*32;ADDRESS='utest1'+'q'*100


class Rpc(fixtures.Rpc):
    def __init__(self):
        super().__init__();self.native=super().call('z_listaccounts');self.next=B
    def call(self,method,params=None):
        if method=='z_listaccounts':return copy.deepcopy(self.native)
        if method=='z_getnewaccount':
            self.native.append({'account_uuid':self.next,'name':params[0],'seedfp':'public-fingerprint','zip32_account_index':len(self.native)})
            return {'account_uuid':self.next}
        if method=='z_getaddressforaccount':return {'account_uuid':params[0],'diversifier_index':params[2],'receiver_types':['orchard'],'address':ADDRESS}
        return super().call(method,params)


class Snapshots:
    def __init__(self):self.rows={};self.fail=False;self.writes=0
    def write(self,binding):
        self.writes+=1
        if self.fail:raise OSError('PRIVATE_ERROR_MUST_NOT_ESCAPE')
        name=binding['account']+'.tar.age';sha=hashlib.sha256(json.dumps(binding,sort_keys=True).encode()).hexdigest()
        self.rows[name]=sha;return name,sha
    def verify(self,name,sha):return self.rows.get(name)==sha


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.rpc=Rpc();self.snapshots=Snapshots()
        self.control={'enabled':True,'depositsEnabled':True,'bindingBackupVerified':True,'accountsSha256':enrollment.digest_accounts([A]),
            'enrollment':{'enabled':True,'baselineAccounts':[A],'recipient':'age1'+'q'*58,
                'welcome':{'enabled':True,'sponsorAccount':A,'amountZat':300000,'maxFeeZat':100000,'maximumGrants':12}}}
        self.service=broker.Transfers({'seedFingerprintHex':'11'*32,'localSeedRecoveryVerified':True},':memory:',self.rpc,lambda _:True,lambda:self.control)
        self.service.db.execute("INSERT INTO account_intents VALUES (?,'created',?)",('tip-'+'1'*32,A))
        self.service.enrollment.snapshots=self.snapshots
        self.binding={'account':B,'userId':'200','label':LABEL,'address':ADDRESS}
    def tearDown(self):self.service.close()
    def create(self):return self.service.create(LABEL)
    def admit(self):self.create();return self.service.admit(self.binding)

    def test_new_account_does_not_invalidate_original_coverage_or_enable_spending_early(self):
        self.create();health=self.service.health()
        self.assertTrue(health['transfersEnabled']);self.assertTrue(health['accountCreationReady'])
        with self.assertRaises(broker.BrokerError):self.service.identity(B)
        self.assertEqual(self.service.identity(A)['account_uuid'],A)
        self.assertFalse(any(m in ('pczt_sign','sendrawtransaction') for m,_ in self.rpc.calls))

    def test_native_default_address_collision_is_avoided_with_one_fixed_shielded_index(self):
        original=self.rpc.call;indices=[]
        def call(method,params=None):
            if method=='z_getaddressforaccount':
                indices.append(params[2])
                if params[2]==0:raise broker.NativeError(-4)
            return original(method,params)
        self.rpc.call=call
        self.assertEqual(self.service.health()['receivingIndex'],4294967296)
        self.admit();self.assertEqual(indices,[4294967296])
        metadata=json.loads(self.service.db.execute('SELECT metadata FROM enrollment_bindings').fetchone()[0])
        self.assertEqual(metadata['diversifier_index'],4294967296)
        self.assertEqual(len(self.rpc.native),2)

    def test_admission_requires_durable_encryption_and_never_reveals_metadata(self):
        value=self.admit();self.assertEqual(value,{'network':'testnet','account':B,'admitted':True})
        self.assertNotIn('public-fingerprint',json.dumps(value));self.assertNotIn('200',json.dumps(value))
        self.assertTrue(self.service.enrollment.admitted(B));self.assertEqual(self.snapshots.writes,1)
        self.assertEqual(self.service.admit(self.binding),value);self.assertEqual(self.snapshots.writes,1)
        self.assertEqual(self.service.db.execute('SELECT COUNT(*) FROM welcome_grants').fetchone()[0],1)

    def test_encryption_failure_leaves_new_account_unfunded_and_preserves_existing_accounts(self):
        self.create();self.snapshots.fail=True
        with self.assertRaises(broker.BrokerError):self.service.admit(self.binding)
        self.assertFalse(self.service.enrollment.admitted(B));self.assertTrue(self.service.health()['transfersEnabled'])
        self.assertEqual(self.service.db.execute('SELECT COUNT(*) FROM welcome_grants').fetchone()[0],0)
        with self.assertRaises(broker.BrokerError):self.service.admit(self.binding)
        self.assertEqual(self.snapshots.writes,1)

    def test_lost_or_changed_encrypted_snapshot_holds_spending(self):
        self.admit();self.snapshots.rows.clear()
        self.assertFalse(self.service.health()['transfersEnabled'])
        with self.assertRaises(broker.BrokerError):self.service.identity(B)

    def test_wrong_owner_label_address_and_seed_are_rejected_before_snapshot(self):
        self.create()
        with self.assertRaises(broker.BrokerError):self.service.admit({**self.binding,'address':'utest1'+'p'*100})
        with self.assertRaises(broker.BrokerError):self.service.admit({**self.binding,'label':'tip-'+'3'*32})
        self.rpc.native[-1]['seedfp']='another-seed'
        with self.assertRaises(broker.BrokerError):self.service.admit(self.binding)
        self.assertEqual(self.snapshots.writes,0)

    def test_owner_cannot_be_reassigned_or_use_the_same_identity_for_two_wallets(self):
        self.admit()
        with self.assertRaises(broker.BrokerError):self.service.admit({**self.binding,'userId':'300'})
        self.rpc.next=C;label='tip-'+'3'*32;self.service.create(label)
        with self.assertRaises(Exception):self.service.admit({**self.binding,'account':C,'label':label})
        self.assertEqual(self.snapshots.writes,1)

    def test_baseline_accounts_cannot_claim_welcome_grants(self):
        with self.assertRaises(broker.BrokerError):self.service.admit({**self.binding,'account':A,'label':'tip-'+'1'*32})
        self.assertEqual(self.service.db.execute('SELECT COUNT(*) FROM welcome_grants').fetchone()[0],0)

    def test_disabled_enrollment_keeps_existing_wallets_and_closes_new_mutations(self):
        self.control['enrollment']['enabled']=False
        self.assertFalse(self.service.health()['accountCreationReady']);self.assertTrue(self.service.health()['transfersEnabled'])
        with self.assertRaises(broker.BrokerError):self.create()
        self.assertEqual(len(self.rpc.native),1)

    def test_invalid_baseline_or_grant_limits_fail_closed(self):
        for key,value in [('amountZat',300001),('maxFeeZat',100001),('maximumGrants',13),('sponsorAccount',B)]:
            options=copy.deepcopy(self.control);options['enrollment']['welcome'][key]=value
            with self.assertRaises(enrollment.EnrollmentError):enrollment.policy(options)
        self.control['enrollment']['baselineAccounts']=[B]
        self.assertFalse(self.service.health()['transfersEnabled']);self.assertFalse(self.service.health()['accountCreationReady'])

    def test_paused_enrollment_does_not_prevent_reconciling_an_existing_created_label(self):
        self.create()
        self.control['enrollment']['enabled']=False
        self.assertFalse(self.service.health()['accountCreationReady'])
        self.assertEqual(self.create(),{'account':B})

    def test_more_than_sixteen_wallets_can_enroll_but_only_twelve_grants_are_reserved(self):
        original=copy.deepcopy(self.control)
        bindings=[];addresses={};native_call=self.rpc.call
        def call(method,params=None):
            result=native_call(method,params)
            if method=='z_getaddressforaccount':result['address']=addresses[params[0]]
            return result
        self.rpc.call=call
        for n in range(2,20):
            account=f'{n:08x}-1111-4111-8111-{n:012x}'
            addresses[account]='utest1'+f'{n:0100x}'
            label='tip-'+f'{n:032x}';self.rpc.next=account
            self.assertTrue(self.service.health()['accountCreationReady'])
            self.assertFalse(self.service.health()['accountCountCapEnabled'])
            self.assertEqual(self.service.create(label),{'account':account})
            binding={'account':account,'userId':str(1000+n),'label':label,'address':addresses[account]}
            self.assertTrue(self.service.admit(binding)['admitted']);bindings.append(binding)
        self.assertEqual(len(self.rpc.native),19)
        self.assertEqual(self.snapshots.writes,18)
        self.assertTrue(self.service.health()['accountCreationReady'])
        rows=self.service.db.execute('SELECT request FROM welcome_grants').fetchall()
        self.assertEqual(len(rows),12)
        requests=[json.loads(row[0]) for row in rows]
        self.assertEqual(sum(r['amountZat']+r['maxFeeZat'] for r in requests),4_800_000)
        self.assertTrue(all(r['amountZat']==300_000 and r['maxFeeZat']==100_000 and r['account']==A for r in requests))
        for binding in bindings:self.assertTrue(self.service.admit(binding)['admitted'])
        self.assertEqual(self.service.db.execute('SELECT COUNT(*) FROM welcome_grants').fetchone()[0],12)
        self.assertEqual(self.snapshots.writes,18)
        self.assertEqual(self.control,original)
        self.assertFalse(any(method in ('pczt_sign','sendrawtransaction') for method,_ in self.rpc.calls))

    def test_native_account_discovery_and_balance_reads_continue_beyond_one_hundred_accounts(self):
        for n in range(2,152):
            self.rpc.native.append({'account_uuid':f'{n:08x}-1111-4111-8111-{n:012x}',
                'name':'tip-'+f'{n:032x}','seedfp':'public-fingerprint','zip32_account_index':n})
        self.assertEqual(len(self.service.accounts()),151)
        self.assertTrue(self.service.health()['accountCreationReady'])
        rows=[{'account_uuid':row['account_uuid'],'orchard':{'spendable':{'valueZat':7}}} for row in self.rpc.native]
        result=broker.read_account_balance({'accounts':rows},A,3)
        self.assertEqual(result['account'],A);self.assertEqual(result['confirmedSpendableZat'],7)
        with self.assertRaises(broker.BrokerError):broker.read_account_balance({'accounts':rows+[rows[0]]},A,3)
        self.rpc.native[-1]['account_uuid']='invalid-account'
        with self.assertRaises(broker.BrokerError):self.service.accounts()

    def test_grant_has_fixed_terms_one_key_and_no_public_post(self):
        self.admit();row=self.service.db.execute('SELECT request FROM welcome_grants').fetchone()
        value=json.loads(row[0]);self.assertEqual(value['amountZat'],300000);self.assertEqual(value['maxFeeZat'],100000)
        self.assertEqual(value['account'],A);self.assertEqual(value['recipient'],ADDRESS)
        self.rpc.inspection['ironwood']['outputs'][0]['value_zat']=300000
        self.service.enrollment.process_one();self.service.enrollment.process_one()
        self.assertEqual(sum(m=='pczt_sign' for m,_ in self.rpc.calls),1)
        self.assertEqual(sum(m=='sendrawtransaction' for m,_ in self.rpc.calls),1)
        self.assertEqual(self.service.enrollment.status(B)['state'],'submitted')

    def test_grant_display_follows_actual_confirmations_and_never_restarts_for_a_zero_balance(self):
        self.admit();self.rpc.inspection['ironwood']['outputs'][0]['value_zat']=300000;self.service.enrollment.process_one()
        self.rpc.confirmed=10
        status=self.service.enrollment.status(B);self.assertEqual(status['state'],'confirmed');self.assertEqual(status['confirmations'],10)
        self.rpc.fail='getrawtransaction';self.assertEqual(self.service.enrollment.status(B)['state'],'unknown');self.rpc.fail=None
        self.rpc.confirmed=2;self.assertEqual(self.service.enrollment.status(B)['state'],'submitted')
        self.control['minimumConfirmations']=3;self.rpc.confirmed=3
        self.assertEqual(self.service.enrollment.status(B)['state'],'confirmed');self.assertEqual(self.service.enrollment.status(B)['minimumConfirmations'],3)
        self.service.enrollment.process_one();self.assertEqual(sum(m=='sendrawtransaction' for m,_ in self.rpc.calls),1)

    def test_unknown_signing_is_held_and_never_repeated(self):
        self.admit();self.rpc.inspection['ironwood']['outputs'][0]['value_zat']=300000;self.rpc.fail='pczt_sign'
        self.service.enrollment.process_one();self.rpc.fail=None;self.service.enrollment.process_one()
        self.assertEqual(self.service.enrollment.status(B)['state'],'review')
        self.assertEqual(sum(m=='pczt_sign' for m,_ in self.rpc.calls),1)
        self.assertFalse(any(m=='sendrawtransaction' for m,_ in self.rpc.calls))

    def test_unknown_broadcast_recovers_only_identical_bytes(self):
        self.admit();self.rpc.inspection['ironwood']['outputs'][0]['value_zat']=300000;self.rpc.fail='sendrawtransaction'
        self.service.enrollment.process_one();self.rpc.fail=None;self.service.enrollment.process_one()
        self.assertEqual(sum(m=='pczt_sign' for m,_ in self.rpc.calls),1)
        calls=[p for m,p in self.rpc.calls if m=='sendrawtransaction'];self.assertEqual(calls,[['abcdef'],['abcdef']])
        self.assertEqual(self.service.enrollment.status(B)['state'],'submitted')

    def test_grant_limit_is_lifetime_and_not_reset_by_restart_or_failure(self):
        self.control['enrollment']['welcome']['maximumGrants']=1;self.admit()
        self.service.db.execute("UPDATE welcome_grants SET phase='failed'")
        self.service.enrollment.queue_grant({**self.binding,'account':C,'userId':'300'},self.control['enrollment'])
        self.assertEqual(self.service.db.execute('SELECT COUNT(*) FROM welcome_grants').fetchone()[0],1)

    def test_disabled_grants_and_unrelated_uncertain_spends_do_not_sign(self):
        self.admit();self.control['enrollment']['welcome']['enabled']=False;self.service.enrollment.process_one()
        self.control['enrollment']['welcome']['enabled']=True
        self.service.db.execute("INSERT INTO transfer_jobs VALUES (?,?,'{}','review',NULL,NULL,NULL,'today',1)",('e'*64,'f'*48))
        self.service.enrollment.process_one()
        self.assertFalse(any(m in ('pczt_create','pczt_sign','sendrawtransaction') for m,_ in self.rpc.calls))

    def test_encrypted_snapshot_integrity_checks_and_path_traversal(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            storage=enrollment.Snapshots(self.service,'age1'+'q'*58,Path(folder));path=Path(folder)/(B+'.tar.age')
            path.write_bytes(b'age-encryption.org/v1\nSYNTHETIC_CIPHERTEXT');path.chmod(0o600)
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            # Windows does not implement Unix permission bits. Verify path/hash
            # handling independently; private Linux modes are exercised at install.
            if __import__('os').name=='posix':self.assertTrue(storage.verify(path.name,digest))
            self.assertFalse(storage.verify('../private',digest));self.assertFalse(storage.verify(path.name,'0'*64))


if __name__=='__main__':unittest.main()
