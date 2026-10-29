from contextlib import closing
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import unittest
import uuid

spec=importlib.util.spec_from_file_location('enrollment_backup_verify',Path(__file__).with_name('verify-enrollment-backups-local.py'))
verify=importlib.util.module_from_spec(spec);spec.loader.exec_module(verify)
A='22222222-2222-4222-8222-222222222222';SEED='11'*32


class BackupVerificationTests(unittest.TestCase):
    def fixture(self):
        binding={'account':A,'userId':'200','label':'tip-'+'2'*32,'address':'utest1'+'q'*100,
                 'network':'testnet','seedfp':'public-synthetic-fingerprint','zip32_account_index':2,'birthday_height':0}
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE accounts(uuid BLOB)');db.execute('INSERT INTO accounts VALUES (?)',(uuid.UUID(A).bytes,))
            db.execute('CREATE TABLE ext_zallet_keystore_mnemonics(hd_seed_fingerprint BLOB,backup_confirmed INTEGER)')
            db.execute('INSERT INTO ext_zallet_keystore_mnemonics VALUES (?,1)',(bytes.fromhex(SEED),));wallet=db.serialize()
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE account_intents(label TEXT,phase TEXT,account TEXT)')
            db.execute('INSERT INTO account_intents VALUES (?,\'created\',?)',(binding['label'],A))
            db.execute('CREATE TABLE enrollment_bindings(account TEXT,user_id TEXT,label TEXT,address TEXT,metadata TEXT)')
            db.execute('INSERT INTO enrollment_bindings VALUES (?,?,?,?,?)',(A,binding['userId'],binding['label'],binding['address'],json.dumps(binding)));broker=db.serialize()
        return {'wallet.db':wallet,'broker.db':broker,'binding.json':json.dumps(binding).encode()},binding

    def archive(self,files):
        files={**files,'manifest.json':json.dumps({'network':'testnet','containsEncryptionIdentity':False,
            'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}}).encode()}
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w') as archive:
            for name,raw in files.items():
                entry=tarfile.TarInfo(name);entry.size=len(raw);archive.addfile(entry,io.BytesIO(raw))
        return output.getvalue()

    def test_consistent_native_and_private_identity_binding_verifies_without_output(self):
        files,binding=self.fixture();parsed,record=verify.snapshot(self.archive(files))
        self.assertTrue(verify.check_binding(parsed,record,SEED))

    def test_wrong_identity_account_seed_or_mapping_is_rejected(self):
        files,binding=self.fixture()
        for change in ({'userId':'300'},{'account':'33333333-3333-4333-8333-333333333333'},{'birthday_height':1},{'address':'utest1wrong'}):
            with self.assertRaises(ValueError):verify.check_binding(files,{**binding,**change},SEED)
        with self.assertRaises(ValueError):verify.check_binding(files,binding,'22'*32)

    def test_unknown_or_private_key_entries_and_tampered_snapshot_fail(self):
        files,_=self.fixture()
        with self.assertRaises(ValueError):verify.snapshot(self.archive({**files,'encryption-identity.txt':b'NOT A REAL KEY'}))
        changed=bytearray(self.archive(files));offset=changed.find(b'SQLite format 3');changed[offset+40]^=1
        with self.assertRaises(ValueError):verify.snapshot(bytes(changed))


if __name__=='__main__':unittest.main()
