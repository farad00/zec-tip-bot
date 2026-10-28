import copy
import importlib.util
from pathlib import Path
import time
import unittest

spec = importlib.util.spec_from_file_location('account_broker', Path(__file__).parent.parent / 'deploy/wallet-account-broker.py')
broker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)
HASH = 'a' * 64
HEIGHT = 4551200
LABEL = 'tip-' + 'b' * 32
ACCOUNT = '11111111-1111-4111-8111-111111111111'


class Rpc:
    def __init__(self):
        self.native = []
        self.calls = []
        self.lose_result = False
        self.tip = {'chain': 'test', 'blocks': HEIGHT, 'bestblockhash': HASH, 'verificationprogress': 1,
                    'upgrades': {'77190ad9': {'activationheight': 4465026, 'status': 'active'}}}
        self.header = {'height': HEIGHT, 'hash': HASH, 'time': int(time.time())}
        self.status = {'node_tip': {'height': HEIGHT}, 'wallet_tip': {'height': HEIGHT}, 'locked': True}
        self.receivers = ['orchard']

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == 'getblockchaininfo': return self.tip
        if method == 'getblockheader': return self.header
        if method == 'getwalletstatus': return self.status
        if method == 'z_listaccounts': return self.native
        if method == 'z_getnewaccount':
            self.native.append({'account_uuid': ACCOUNT, 'name': params[0], 'seedfp': 'SENSITIVE',
                                'addresses': ['PRIVATE_ADDRESS']})
            self.status['locked'] = False
            if self.lose_result: raise TimeoutError('NEVER_SHOW_NATIVE_DETAILS')
            return {'account_uuid': ACCOUNT}
        if method == 'z_getaddressforaccount':
            return {'account_uuid': ACCOUNT, 'diversifier_index': 0,
                    'receiver_types': self.receivers, 'address': 'utest1PUBLIC_SYNTHETIC_TEST_ONLY'}
        if method == 'z_getbalanceforaccount':
            return {'pools':{'ironwood':{'valueZat':12345}},'minimum_confirmations':10}
        raise AssertionError('Unexpected method: ' + method)


class AccountBrokerTests(unittest.TestCase):
    def setUp(self):
        self.rpc = Rpc()
        self.backup = True
        self.config = {'seedFingerprintHex': 'A1' * 32, 'localSeedRecoveryVerified': True}
        self.service = broker.Accounts(self.config, ':memory:', self.rpc, lambda fp: self.backup)

    def tearDown(self):
        self.service.close()

    def test_empty_wallet_can_create_account_without_claiming_synced_or_enabling_funds(self):
        health = self.service.health()
        self.assertTrue(health['accountCreationReady'])
        self.assertTrue(health['emptyWalletBootstrap'])
        self.assertFalse(health['synced'])
        self.assertFalse(health['depositsEnabled'])
        self.assertFalse(health['transfersEnabled'])

    def test_nonempty_locked_wallet_cannot_use_empty_bootstrap(self):
        self.rpc.native.append({'account_uuid': ACCOUNT, 'name': LABEL})
        self.assertFalse(self.service.health()['accountCreationReady'])
        self.assertFalse(self.service.health()['emptyWalletBootstrap'])

    def test_current_node_checks_require_fresh_correct_testnet_consensus_and_header(self):
        self.assertTrue(broker.chain_is_current(self.rpc.tip, self.rpc.header, time.time()))
        for field, value in [('time', int(time.time()) - 3600), ('hash', 'c' * 64), ('height', HEIGHT-1)]:
            header = {**self.rpc.header, field: value}
            self.assertFalse(broker.chain_is_current(self.rpc.tip, header, time.time()))
        for change in ({'chain': 'main'}, {'verificationprogress': .9}, {'upgrades': None}):
            self.assertFalse(broker.chain_is_current({**self.rpc.tip, **change}, self.rpc.header, time.time()))

    def test_unconfirmed_recovery_or_stale_chain_blocks_before_native_mutation(self):
        self.backup = False
        with self.assertRaises(broker.BrokerError): self.service.create(LABEL)
        self.backup = True
        self.rpc.header['time'] -= 3600
        with self.assertRaises(broker.BrokerError): self.service.create(LABEL)
        self.assertNotIn('z_getnewaccount', [method for method, _ in self.rpc.calls])

    def test_account_creation_is_idempotent_and_never_discloses_native_extra_fields(self):
        first = self.service.create(LABEL)
        self.assertEqual(first, {'account': ACCOUNT})
        self.assertEqual(self.service.create(LABEL), first)
        self.assertEqual(len([call for call in self.rpc.calls if call[0] == 'z_getnewaccount']), 1)
        self.assertEqual(self.service.accounts(), [{'account': ACCOUNT, 'name': LABEL}])
        self.assertNotIn('SENSITIVE', str(self.service.accounts()))
        self.assertNotIn('PRIVATE_ADDRESS', str(self.service.accounts()))

    def test_lost_creation_reply_reconciles_by_label_without_repeating_mutation(self):
        self.rpc.lose_result = True
        with self.assertRaises(broker.BrokerError) as error: self.service.create(LABEL)
        self.assertEqual(error.exception.code, 'account_review_required')
        self.assertEqual(self.service.create(LABEL), {'account': ACCOUNT})
        self.assertEqual(len([call for call in self.rpc.calls if call[0] == 'z_getnewaccount']), 1)

    def test_uncertain_creation_without_native_match_is_held(self):
        self.service.db.execute("INSERT INTO account_intents VALUES (?,'creating',NULL)", (LABEL,))
        with self.assertRaises(broker.BrokerError): self.service.create(LABEL)
        self.assertNotIn('z_getnewaccount', [method for method, _ in self.rpc.calls])

    def test_only_bound_accounts_and_fixed_shielded_receiver_index_are_permitted(self):
        with self.assertRaises(broker.BrokerError): self.service.address(ACCOUNT, 0)
        self.service.create(LABEL)
        result = self.service.address(ACCOUNT, 0)
        self.assertEqual(result['network'], 'testnet')
        self.assertEqual(self.rpc.calls[-1], ('z_getaddressforaccount', [ACCOUNT, ['orchard'], 0]))
        for index in (1, False, None):
            with self.assertRaises(broker.BrokerError): self.service.address(ACCOUNT, index)
        self.rpc.receivers = ['orchard', 'p2pkh']
        with self.assertRaises(broker.BrokerError): self.service.address(ACCOUNT, 0)

    def test_balance_requires_bound_account_and_synced_wallet_and_includes_ironwood(self):
        with self.assertRaises(broker.BrokerError):self.service.balance(ACCOUNT)
        self.service.create(LABEL)
        self.assertEqual(self.service.balance(ACCOUNT)['confirmedSpendableZat'],12345)
        self.assertEqual(self.rpc.calls[-1],('z_getbalanceforaccount',[ACCOUNT,10]))
        self.rpc.status['locked']=True
        with self.assertRaises(broker.BrokerError):self.service.balance(ACCOUNT)

    def test_balance_parsing_does_not_count_transparent_funds_or_accept_wrong_confirmation_policy(self):
        raw={'pools':{'sapling':{'valueZat':1},'orchard':{'valueZat':2},'ironwood':{'valueZat':3},
                      'transparent':{'valueZat':100}},'minimum_confirmations':10}
        self.assertEqual(broker.read_balance(raw,ACCOUNT)['confirmedSpendableZat'],6)
        self.assertTrue(broker.read_balance(raw,ACCOUNT)['transparentBalancePresent'])
        for altered in ({**raw,'minimum_confirmations':1},{**raw,'pools':{'ironwood':{'valueZat':-1}}},
                        {**raw,'pools':{'unknown':{'valueZat':1}}}):
            with self.assertRaises(broker.BrokerError):broker.read_balance(altered,ACCOUNT)


if __name__ == '__main__': unittest.main()
