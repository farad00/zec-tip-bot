import base64
from contextlib import closing
import copy
import hashlib
import importlib.util
import json
import io
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec=importlib.util.spec_from_file_location('transfer_broker',Path(__file__).parent.parent/'deploy/wallet-transfer-broker.py')
broker=importlib.util.module_from_spec(spec);spec.loader.exec_module(broker)
A='11111111-1111-4111-8111-111111111111'
ADDRESS='utest1'+'q'*100
PCZT=base64.b64encode(b'SYNTHETIC PCZT FIXTURE').decode()
TXID='a'*64
HEIGHT=4551424


class Rpc:
    def __init__(self):
        self.calls=[];self.confirmed=None;self.fail=None;self.lost_broadcast=False
        self.inspection={'wallet_created':True,'privacy_policy':'FullPrivacy','consensus_branch_id':'77190ad9',
            'tx_version':6,'expiry_height':HEIGHT+40,'fee_zat':10000,
            'signing_hints':{'seed_fingerprint':'public-fingerprint','account_index':0},
            'transparent':{'inputs':[],'outputs':[]},
            'sapling':{'outputs':[],'value_balance_zat':0,'proofs_complete':True},
            'orchard':{'outputs':[],'value_balance_zat':0,'proof_complete':True},
            'ironwood':{'outputs':[{'user_address':ADDRESS,'value_zat':100000},{'value_zat':50000}],
                        'value_balance_zat':10000,'proof_complete':True}}
    def call(self,method,params=None):
        self.calls.append((method,params))
        if method==self.fail:raise TimeoutError('PRIVATE_NATIVE_DIAGNOSTIC')
        if method=='getblockchaininfo':return {'chain':'test','blocks':HEIGHT,'bestblockhash':'b'*64,'verificationprogress':1,
                    'upgrades':{'77190ad9':{'activationheight':4465026,'status':'active'}}}
        if method=='getblockheader':return {'height':HEIGHT,'hash':'b'*64,'time':int(time.time())}
        if method=='getwalletstatus':return {'node_tip':{'height':HEIGHT},'wallet_tip':{'height':HEIGHT},'locked':False}
        if method=='z_listaccounts':return [{'account_uuid':A,'name':'tip-'+'1'*32,'seedfp':'public-fingerprint','zip32_account_index':0}]
        if method=='z_listunifiedreceivers':return {'orchard':ADDRESS}
        if method=='pczt_create':return {'pczt':PCZT,'privacy_policy':'FullPrivacy'}
        if method=='pczt_inspect':return copy.deepcopy(self.inspection)
        if method=='pczt_prove':return {'pczt':PCZT}
        if method=='pczt_sign':return {'pczt':PCZT,**{'unsigned_'+p:[] for p in ('transparent','sapling','orchard','ironwood')}}
        if method=='pczt_extract':return {'hex':'abcdef','txid':TXID,'stored':True}
        if method=='decoderawtransaction':return {'txid':TXID,'version':6,'vin':[],'vout':[],'expiryheight':HEIGHT+40}
        if method=='getrawtransaction':
            if self.confirmed is None:raise broker.NativeError(-5)
            return {'txid':TXID,'confirmations':self.confirmed}
        if method=='sendrawtransaction':
            self.confirmed=0
            if self.lost_broadcast:raise TimeoutError('POST_RESPONSE_LOST')
            return TXID
        raise AssertionError(method)


class TransferBrokerTests(unittest.TestCase):
    def setUp(self):
        self.rpc=Rpc();self.enabled=True
        self.config={'seedFingerprintHex':'11'*32,'localSeedRecoveryVerified':True}
        digest=hashlib.sha256(A.encode()).hexdigest()
        self.control=lambda:{'enabled':self.enabled,'depositsEnabled':False,'accountsSha256':digest}
        self.service=broker.Transfers(self.config,':memory:',self.rpc,lambda _:True,self.control)
        self.service.db.execute("INSERT INTO account_intents VALUES (?,'created',?)",('tip-'+'1'*32,A))
        self.request={'idempotencyKey':'1'*64,'account':A,'recipient':ADDRESS,'amountZat':100000,'maxFeeZat':10000}
    def tearDown(self):self.service.close()

    def test_receipts_are_account_bound_redacted_and_exclude_change_and_known_bot_transfers(self):
        row={'account_uuid':A,'txid':'c'*64,'mined_height':HEIGHT-9,'account_balance_delta':100000,
             'expired_unmined':False,'block_time':1700000000,'outputs':[
                 {'to_account':A,'pool':'ironwood','is_change':False,'value':100000,'to_address':ADDRESS,'memo':'PRIVATE MEMO'},
                 {'to_account':A,'pool':'ironwood','is_change':True,'value':50000}]}
        receipts=broker.read_receipts([row],A,HEIGHT,set())
        self.assertEqual(len(receipts),1);self.assertEqual(receipts[0]['amountZat'],100000);self.assertEqual(receipts[0]['confirmations'],10)
        rendered=json.dumps(receipts)
        for private in (ADDRESS,A,'PRIVATE MEMO','c'*64):self.assertNotIn(private,rendered)
        self.assertEqual(broker.read_receipts([row],A,HEIGHT,{'c'*64}),[])
        other=copy.deepcopy(row);other['account_uuid']='22222222-2222-4222-8222-222222222222'
        with self.assertRaises(broker.BrokerError):broker.read_receipts([other],A,HEIGHT,set())

    def test_receipts_keep_pending_and_expired_states_distinct(self):
        row={'account_uuid':A,'txid':'c'*64,'mined_height':None,'account_balance_delta':100000,
             'expired_unmined':False,'block_time':None,'outputs':[{'to_account':A,'pool':'orchard','is_change':False,'value':100000}]}
        value=broker.read_receipts([row],A,HEIGHT,set())[0]
        self.assertEqual(value['confirmations'],0);self.assertIsNone(value['time'])
        row['expired_unmined']=True;self.assertEqual(broker.read_receipts([row],A,HEIGHT,set()),[])

    def test_receipt_reads_never_reveal_wrong_accounts_or_invalid_values(self):
        row={'account_uuid':A,'txid':'c'*64,'mined_height':HEIGHT,'account_balance_delta':100000,
             'expired_unmined':False,'block_time':1700000000,'outputs':[{'to_account':A,'pool':'orchard','is_change':False,'value':100000}]}
        for mutate in (lambda v:v.update(mined_height=HEIGHT+1),lambda v:v['outputs'][0].update(value='100000')):
            value=copy.deepcopy(row);mutate(value)
            with self.assertRaises(broker.BrokerError):broker.read_receipts([value],A,HEIGHT,set())
        with self.assertRaises(broker.BrokerError):broker.read_receipts([row,row],A,HEIGHT,set())

    def prepare(self):return self.service.prepare(self.request)
    def signed(self):
        p=self.prepare();return self.service.sign({'idempotencyKey':self.request['idempotencyKey'],'proposal':p['proposal']})

    def test_exact_decimal_full_privacy_opaque_proposal_and_repeated_payment_are_idempotent(self):
        p=self.prepare();self.assertEqual(self.prepare(),p)
        self.assertNotIn(PCZT,json.dumps(p));self.assertTrue(broker.TOKEN.fullmatch(p['proposal']))
        create=[params for method,params in self.rpc.calls if method=='pczt_create']
        self.assertEqual(create,[[A,[{'address':ADDRESS,'amount':'0.00100000'}],10,'FullPrivacy']])
        signed=self.signed();self.assertEqual(self.signed(),signed)
        self.assertEqual(sum(method=='pczt_sign' for method,_ in self.rpc.calls),1)
        self.assertEqual(self.service.broadcast(signed),{'txid':TXID})
        self.service.broadcast(signed)
        self.assertEqual(sum(method=='sendrawtransaction' for method,_ in self.rpc.calls),1)
        self.rpc.confirmed=3;self.assertEqual(self.service.confirmations(TXID),{'confirmations':3})

    def test_one_zatoshi_is_transmitted_as_a_decimal_string(self):
        self.request['amountZat']=1;self.rpc.inspection['ironwood']['outputs'][0]['value_zat']=1
        self.prepare()
        args=next(p for m,p in self.rpc.calls if m=='pczt_create')
        self.assertEqual(args[1][0]['amount'],'0.00000001')

    def test_altered_fee_amount_recipient_privacy_identity_or_transparent_io_never_signs(self):
        original=copy.deepcopy(self.rpc.inspection)
        for mutate in (
            lambda v:v.update(fee_zat=10001),lambda v:v.update(privacy_policy='AllowRevealedAmounts'),
            lambda v:v.update(wallet_created=False),lambda v:v.update(consensus_branch_id='00000000'),
            lambda v:v['signing_hints'].update(account_index=1),
            lambda v:v['transparent']['outputs'].append({'value_zat':1}),
            lambda v:v['ironwood']['outputs'][0].update(value_zat=99999),
            lambda v:v['ironwood']['outputs'][0].update(user_address='utest1'+'p'*100),
            lambda v:v.update(expiry_height=HEIGHT+1)):
            with self.subTest(mutate=mutate):
                self.rpc.inspection=copy.deepcopy(original);mutate(self.rpc.inspection)
                self.service.db.execute('DELETE FROM transfer_jobs')
                with self.assertRaises(broker.BrokerError):self.prepare()
                self.assertEqual(self.service.row(key=self.request['idempotencyKey'])['phase'],
                                 'rejected' if self.rpc.inspection['fee_zat']>self.request['maxFeeZat'] else 'review')
        self.assertFalse(any(m=='pczt_sign' for m,_ in self.rpc.calls))

    def test_changed_proposal_after_proving_is_held_before_signature(self):
        p=self.prepare();original=self.rpc.call
        def call(method,params=None):
            if method=='pczt_prove':self.rpc.inspection['ironwood']['outputs'][0]['value_zat']-=1
            return original(method,params)
        self.rpc.call=call
        with self.assertRaises(broker.BrokerError):self.service.sign({'idempotencyKey':self.request['idempotencyKey'],'proposal':p['proposal']})
        self.assertFalse(any(m=='pczt_sign' for m,_ in self.rpc.calls))
        self.assertEqual(self.service.row(key=self.request['idempotencyKey'])['phase'],'review')

    def test_lost_broadcast_response_is_reconciled_without_new_proposal_or_signature(self):
        signed=self.signed();self.rpc.lost_broadcast=True
        self.assertEqual(self.service.broadcast(signed),{'txid':TXID})
        self.assertEqual(sum(m=='pczt_create' for m,_ in self.rpc.calls),1)
        self.assertEqual(sum(m=='pczt_sign' for m,_ in self.rpc.calls),1)

    def test_unknown_broadcast_preserves_exact_bytes_for_retry(self):
        signed=self.signed();self.rpc.fail='sendrawtransaction'
        with self.assertRaises(TimeoutError):self.service.broadcast(signed)
        self.assertEqual(self.service.row(key=self.request['idempotencyKey'])['phase'],'broadcasting')
        self.rpc.fail=None;self.service.broadcast(signed)
        attempts=[p for m,p in self.rpc.calls if m=='sendrawtransaction']
        self.assertEqual(attempts,[[signed['raw']],[signed['raw']]])

    def test_timeout_during_signing_is_not_blindly_repeated_and_blocks_overlapping_spends(self):
        p=self.prepare();self.rpc.fail='pczt_sign'
        args={'idempotencyKey':self.request['idempotencyKey'],'proposal':p['proposal']}
        with self.assertRaises(TimeoutError):self.service.sign(args)
        self.rpc.fail=None
        with self.assertRaises(broker.BrokerError):self.service.sign(args)
        other={**self.request,'idempotencyKey':'2'*64}
        with self.assertRaises(broker.BrokerError):self.service.prepare(other)
        self.assertEqual(sum(m=='pczt_sign' for m,_ in self.rpc.calls),1)

    def test_root_hold_backed_up_account_set_and_bound_transaction_are_required(self):
        self.enabled=False
        with self.assertRaises(broker.BrokerError):self.prepare()
        self.enabled=True
        self.service.control=lambda:{'enabled':True,'accountsSha256':'0'*64}
        with self.assertRaises(broker.BrokerError):self.prepare()
        self.service.control=self.control
        signed=self.signed()
        with self.assertRaises(broker.BrokerError):self.service.broadcast({**signed,'raw':'deadbeef'})
        with self.assertRaises(broker.BrokerError):self.service.sign({'idempotencyKey':'2'*64,'proposal':'0'*48})
        self.assertFalse(any(m=='sendrawtransaction' for m,_ in self.rpc.calls))

    def test_fee_only_rejection_stays_unsigned_and_does_not_block_a_new_authorized_request(self):
        self.rpc.inspection['fee_zat']=self.request['maxFeeZat']+1
        with self.assertRaises(broker.BrokerError) as raised:self.prepare()
        self.assertEqual(raised.exception.code,'proven_fee_rejection')
        self.assertEqual(self.service.row(key=self.request['idempotencyKey'])['phase'],'rejected')
        self.assertTrue(self.service.row(key=self.request['idempotencyKey'])['pczt'])
        self.assertFalse(any(m in ('pczt_prove','pczt_sign','pczt_extract','sendrawtransaction') for m,_ in self.rpc.calls))
        self.request={**self.request,'idempotencyKey':'b'*64,'maxFeeZat':100000}
        self.assertEqual(self.prepare()['feeZat'],10001)

    def test_backed_up_pilot_closes_new_enrollment_without_disabling_existing_transfers(self):
        original=self.control
        self.service.control=lambda:{**original(),'bindingBackupVerified':True}
        health=self.service.health()
        self.assertFalse(health['accountCreationReady']);self.assertTrue(health['transfersEnabled'])
        self.assertEqual(self.prepare()['amountZat'],self.request['amountZat'])

    def test_explicit_insufficient_funds_rejection_releases_only_its_reservation(self):
        original=self.rpc.call
        def call(method,params=None):
            if method=='pczt_create':raise broker.NativeError(-6)
            return original(method,params)
        self.rpc.call=call
        with self.assertRaisesRegex(broker.BrokerError,'could not complete'):self.prepare()
        self.assertEqual(self.service.row(key=self.request['idempotencyKey'])['phase'],'rejected')
        self.assertFalse(any(m=='pczt_sign' for m,_ in self.rpc.calls))

    def test_claiming_prepared_payment_is_atomic_and_cannot_reclaim_an_interrupted_step(self):
        p=self.prepare()
        self.assertTrue(self.service.claim_signing(p['proposal']))
        self.assertFalse(self.service.claim_signing(p['proposal']))
        with self.assertRaises(broker.BrokerError):self.service.sign({'idempotencyKey':self.request['idempotencyKey'],'proposal':p['proposal']})
        self.assertFalse(any(m=='pczt_sign' for m,_ in self.rpc.calls))

    def test_idempotency_key_cannot_change_terms_or_accept_imported_pczt(self):
        self.prepare()
        with self.assertRaises(broker.BrokerError):self.service.prepare({**self.request,'amountZat':99999})
        with self.assertRaises(broker.BrokerError):self.service.sign({'idempotencyKey':'1'*64,'proposal':PCZT})
        with self.assertRaises(broker.BrokerError):self.service.prepare({**self.request,'memo':'PUBLIC X ID'})

    def test_signed_bytes_survive_reopening_the_actual_database_without_another_signature(self):
        import tempfile
        import sqlite3
        import shutil
        root=Path(__file__).resolve().parent
        folder=Path(tempfile.mkdtemp(prefix='.broker-durable-',dir=root)).resolve()
        try:
            signed=self.signed();path=folder/'broker.db'
            with closing(sqlite3.connect(path)) as copy:self.service.db.backup(copy)
            restarted=broker.Transfers(self.config,path,self.rpc,lambda _:True,self.control)
            try:
                self.rpc.lost_broadcast=True
                self.assertEqual(restarted.broadcast(signed),{'txid':TXID})
                self.assertEqual(sum(m=='pczt_sign' for m,_ in self.rpc.calls),1)
                self.assertEqual(restarted.row(key=self.request['idempotencyKey'])['phase'],'submitted')
            finally:restarted.close()
        finally:
            if folder.parent!=root:raise ValueError('Unexpected synthetic test cleanup path')
            shutil.rmtree(folder)

    def test_native_transport_uses_only_loopback_and_numeric_correlated_errors(self):
        import tempfile
        import shutil
        root=Path(__file__).resolve().parent;folder=Path(tempfile.mkdtemp(prefix='.broker-transport-',dir=root)).resolve()
        try:
            (folder/'auth.cookie').write_text('synthetic_user:synthetic-cookie')
            calls=[]
            def open_response(request,timeout):
                calls.append(request.full_url)
                payload=json.loads(request.data)
                return io.BytesIO(json.dumps({'id':payload['id'],'error':{'code':-6,'message':'PRIVATE_NATIVE_MESSAGE'}}).encode())
            rpc=broker.NativeRpc();rpc.opener=SimpleNamespace(open=open_response)
            with patch.object(broker.base,'WALLET',folder):
                with self.assertRaises(broker.NativeError) as caught:rpc.call('pczt_create',[])
                self.assertEqual(caught.exception.number,-6);self.assertNotIn('PRIVATE_NATIVE_MESSAGE',str(caught.exception))
                with self.assertRaises(broker.BrokerError):rpc.call('z_exportkey',[])
            self.assertEqual(calls,['http://127.0.0.1:28232/'])
        finally:
            if folder.parent!=root:raise ValueError('Unexpected synthetic test cleanup path')
            shutil.rmtree(folder)


if __name__=='__main__':unittest.main()
