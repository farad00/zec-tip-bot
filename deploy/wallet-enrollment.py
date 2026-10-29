"""Bounded Testnet enrollment and independently budgeted welcome grants."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import threading
import time

ROOT=Path('/var/lib/zec-tip-bot-wallet-broker/enrollment-backups')
WALLET=Path('/var/lib/zec-tip-bot-wallet-nu7-testnet/wallet.db')
UUID=re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')


class EnrollmentError(Exception):
    pass


def file_digest(path):
    result=hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda:file.read(1024*1024),b''):result.update(chunk)
    return result.hexdigest()


def digest_accounts(accounts):
    return hashlib.sha256('\n'.join(sorted(accounts)).encode()).hexdigest()


def policy(control):
    value=control.get('enrollment')
    if value is None:return None
    if (not isinstance(value,dict) or type(value.get('enabled')) is not bool
            or not isinstance(value.get('baselineAccounts'),list) or not 1<=len(value['baselineAccounts'])<=16
            or any(not isinstance(a,str) or not UUID.fullmatch(a) for a in value['baselineAccounts'])
            or len(set(value['baselineAccounts']))!=len(value['baselineAccounts'])
            or digest_accounts(value['baselineAccounts'])!=control.get('accountsSha256')
            or not isinstance(value.get('recipient'),str) or not re.fullmatch(r'age1[0-9a-z]{58}',value['recipient'])):
        raise EnrollmentError('invalid_enrollment_policy')
    grant=value.get('welcome')
    if grant is not None and (not isinstance(grant,dict) or type(grant.get('enabled')) is not bool
            or grant.get('sponsorAccount') not in value['baselineAccounts']
            or grant.get('amountZat')!=300_000 or grant.get('maxFeeZat')!=100_000
            or type(grant.get('maximumGrants')) is not int or not 1<=grant['maximumGrants']<=12):
        raise EnrollmentError('invalid_welcome_policy')
    return value


class Snapshots:
    def __init__(self,service,recipient,root=ROOT):
        self.service,self.recipient,self.root=service,recipient,root
        self.verified={}

    def verify(self,name,sha):
        if (not isinstance(name,str) or not re.fullmatch(r'[a-f0-9-]{36}\.tar\.age',name)
                or not isinstance(sha,str) or not re.fullmatch(r'[a-f0-9]{64}',sha)):return False
        try:
            path=self.root/name;info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_mode&0o077 or info.st_uid!=os.geteuid():return False
            signature=(info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,sha)
            if self.verified.get(name)==signature:return True
            if file_digest(path)!=sha:return False
            self.verified[name]=signature;return True
        except OSError:return False

    def write(self,binding):
        self.root.mkdir(mode=0o700,exist_ok=True)
        info=self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or self.root.is_symlink() or info.st_mode&0o077 or info.st_uid!=os.geteuid():
            raise EnrollmentError('backup_directory_not_private')
        name=binding['account']+'.tar.age';target=self.root/name
        # An interrupted snapshot is never silently replaced with different metadata.
        if target.exists():raise EnrollmentError('backup_review_required')
        with tempfile.TemporaryDirectory(prefix='.stage-',dir=self.root) as temp:
            folder=Path(temp)
            if WALLET.is_symlink() or not WALLET.is_file():raise EnrollmentError('wallet_unavailable')
            with closing(sqlite3.connect(WALLET.as_uri()+'?mode=ro',uri=True,timeout=10)) as source,closing(sqlite3.connect(folder/'wallet.db')) as dest:
                source.backup(dest)
                if dest.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise EnrollmentError('backup_integrity_failed')
            with closing(sqlite3.connect(folder/'broker.db')) as dest:
                self.service.db.backup(dest)
                if dest.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise EnrollmentError('backup_integrity_failed')
            (folder/'binding.json').write_text(json.dumps(binding,sort_keys=True),encoding='utf-8')
            manifest={'schema':1,'network':'testnet','containsEncryptionIdentity':False,
                'offServerVerified':False,'restoreWithWorkersHeld':True,
                'files':{n:file_digest(folder/n) for n in ('wallet.db','broker.db','binding.json')}}
            (folder/'manifest.json').write_text(json.dumps(manifest,sort_keys=True),encoding='utf-8')
            with tarfile.open(folder/'snapshot.tar','w') as archive:
                for n in (*manifest['files'],'manifest.json'):archive.add(folder/n,arcname=n,recursive=False)
            subprocess.run(['/usr/bin/age','--encrypt','--recipient',self.recipient,'--output',str(folder/'snapshot.age'),str(folder/'snapshot.tar')],
                           check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90)
            encrypted=folder/'snapshot.age';encrypted.chmod(0o600)
            with encrypted.open('rb') as file:os.fsync(file.fileno())
            sha=file_digest(encrypted)
            os.replace(encrypted,target)
            descriptor=os.open(self.root,os.O_RDONLY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
        return name,sha


class Enrollment:
    def __init__(self,service,snapshots=None):
        self.service=service;self.snapshots=snapshots;self.storage_cache={}
        service.db.executescript('''
          CREATE TABLE IF NOT EXISTS enrollment_bindings(
            account TEXT PRIMARY KEY,user_id TEXT UNIQUE NOT NULL,label TEXT UNIQUE NOT NULL,
            address TEXT UNIQUE NOT NULL,metadata TEXT NOT NULL,phase TEXT NOT NULL,archive TEXT,sha TEXT);
          CREATE TABLE IF NOT EXISTS welcome_grants(
            account TEXT PRIMARY KEY,user_id TEXT UNIQUE NOT NULL,request_key TEXT UNIQUE NOT NULL,
            request TEXT NOT NULL,phase TEXT NOT NULL);
        ''')
        self.wake=threading.Event();self.stopped=threading.Event();self.thread=None

    def options(self):return policy(self.service.control())

    def storage(self,options):
        if self.snapshots:return self.snapshots
        recipient=options['recipient']
        if recipient not in self.storage_cache:self.storage_cache[recipient]=Snapshots(self.service,recipient)
        return self.storage_cache[recipient]

    def admitted(self,account,options=None):
        options=options or self.options()
        if not options:return True  # Legacy account-set guard is enforced by Transfers.health.
        if account in options['baselineAccounts']:return True
        row=self.service.db.execute('SELECT phase,archive,sha FROM enrollment_bindings WHERE account=?',(account,)).fetchone()
        return bool(row and row[0]=='active' and self.storage(options).verify(row[1],row[2]))

    def admit(self,value):
        if (not isinstance(value,dict) or set(value)!={'account','userId','label','address'}
                or not isinstance(value['account'],str) or not UUID.fullmatch(value['account'])
                or not isinstance(value['userId'],str) or not re.fullmatch(r'[0-9]{1,20}',value['userId'])
                or not isinstance(value['label'],str) or not re.fullmatch(r'tip-[a-f0-9]{32}',value['label'])
                or not isinstance(value['address'],str) or not re.fullmatch(r'utest1[0-9a-z]{20,1994}',value['address'])):
            raise EnrollmentError('invalid_enrollment_request')
        s=self.service
        with s.lock:
            options=self.options()
            if not options:raise EnrollmentError('enrollment_disabled')
            # Existing wallet owners do not need a new snapshot or welcome grant.
            if value['account'] in options['baselineAccounts']:raise EnrollmentError('baseline_account_not_new')
            old=s.db.execute('SELECT user_id,label,address,phase FROM enrollment_bindings WHERE account=?',(value['account'],)).fetchone()
            if old and old[:3]!=(value['userId'],value['label'],value['address']):raise EnrollmentError('binding_conflict')
            if old and self.admitted(value['account'],options):
                self.queue_grant(value,options)
                return {'network':'testnet','account':value['account'],'admitted':True}
            if not options['enabled']:raise EnrollmentError('enrollment_disabled')
            health=s.health()
            if not health['currentConsensusVerified'] or not health['backupConfirmed']:raise EnrollmentError('wallet_not_ready')
            # Derivation is repeatable at the fixed receiver set/index. Never trust a client address.
            # Native account creation may already occupy index zero with its
            # default receiver set. Use a fixed index above the transparent child
            # range, so a shielded-only address cannot collide with that default.
            address=s.address(value['account'],4294967296)
            if address['address']!=value['address']:raise EnrollmentError('address_mismatch')
            rows=s.rpc.call('z_listaccounts',[False])
            found=[row for row in rows if row.get('account_uuid')==value['account'] and row.get('name')==value['label']]
            if len(found)!=1 or not isinstance(found[0].get('seedfp'),str) or type(found[0].get('zip32_account_index')) is not int:
                raise EnrollmentError('native_identity_missing')
            baseline_seeds={r.get('seedfp') for r in rows if r.get('account_uuid') in options['baselineAccounts']}
            if baseline_seeds!={found[0]['seedfp']}:raise EnrollmentError('native_seed_mismatch')
            if not 0<=found[0]['zip32_account_index']<=0x7fffffff:raise EnrollmentError('native_identity_missing')
            # Height zero is conservative recovery metadata; never guess a later birthday.
            binding={**value,'network':'testnet','seedfp':found[0]['seedfp'],
                     'zip32_account_index':found[0]['zip32_account_index'],'birthday_height':0,'diversifier_index':4294967296}
            metadata=json.dumps(binding,sort_keys=True)
            if old:raise EnrollmentError('backup_review_required')
            s.db.execute("INSERT INTO enrollment_bindings VALUES (?,?,?,?,?,'snapshotting',NULL,NULL)",
                         (value['account'],value['userId'],value['label'],value['address'],metadata))
            try:
                name,sha=self.storage(options).write(binding)
                if not self.storage(options).verify(name,sha):raise EnrollmentError('backup_integrity_failed')
                s.db.execute("UPDATE enrollment_bindings SET phase='active',archive=?,sha=? WHERE account=?",(name,sha,value['account']))
            except Exception:
                s.db.execute("UPDATE enrollment_bindings SET phase='review' WHERE account=?",(value['account'],))
                raise EnrollmentError('backup_review_required') from None
            self.queue_grant(value,options)
            return {'network':'testnet','account':value['account'],'admitted':True}

    def queue_grant(self,binding,options):
        grant=options.get('welcome')
        if not grant or not grant['enabled']:return
        s=self.service
        if s.db.execute('SELECT 1 FROM welcome_grants WHERE user_id=? OR account=?',(binding['userId'],binding['account'])).fetchone():return
        if s.db.execute('SELECT COUNT(*) FROM welcome_grants').fetchone()[0]>=grant['maximumGrants']:return
        key=hashlib.sha256(('zec-tip-bot:welcome:'+binding['userId']).encode()).hexdigest()
        request={'idempotencyKey':key,'account':grant['sponsorAccount'],'recipient':binding['address'],
                 'amountZat':grant['amountZat'],'maxFeeZat':grant['maxFeeZat']}
        s.db.execute("INSERT INTO welcome_grants VALUES (?,?,?,?,'queued')",(binding['account'],binding['userId'],key,json.dumps(request,sort_keys=True)))
        self.wake.set()

    def process_one(self):
        s=self.service
        options=self.options();grant=options.get('welcome') if options else None
        if not grant or not grant['enabled'] or not s.health()['transfersEnabled']:return
        with s.lock:
            row=s.db.execute("SELECT account,user_id,request_key,request FROM welcome_grants WHERE phase='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:return
            account,user,key,raw=row;request=json.loads(raw)
            if not self.admitted(account,options):return
            expected={'idempotencyKey':key,'account':grant['sponsorAccount'],'recipient':s.db.execute('SELECT address FROM enrollment_bindings WHERE account=?',(account,)).fetchone()[0],
                      'amountZat':grant['amountZat'],'maxFeeZat':grant['maxFeeZat']}
            if request!=expected:raise EnrollmentError('welcome_terms_changed')
            # Never overlap a grant with an unrelated uncertain spend.
            if s.db.execute("SELECT 1 FROM transfer_jobs WHERE request_key!=? AND phase NOT IN ('submitted','rejected')",(key,)).fetchone():return
        try:
            proposal=s.prepare(request)
            signed=s.sign({'idempotencyKey':key,'proposal':proposal['proposal']})
            s.broadcast(signed)
            with s.lock:s.db.execute("UPDATE welcome_grants SET phase='submitted' WHERE account=?",(account,))
        except Exception:
            with s.lock:
                payment=s.row(key=key)
                if payment:
                    phase='failed' if payment['phase']=='rejected' else 'queued' if payment['phase'] in ('prepared','signed','broadcasting','submitted') else 'review'
                    s.db.execute('UPDATE welcome_grants SET phase=? WHERE account=?',(phase,account))
            # No new idempotency key, replacement payment or automatic signing retry.

    def status(self,account):
        if not isinstance(account,str) or not UUID.fullmatch(account):raise EnrollmentError('invalid_account')
        with self.service.lock:
            row=self.service.db.execute('SELECT phase FROM welcome_grants WHERE account=?',(account,)).fetchone()
        return {'network':'testnet','account':account,'state':row[0] if row else 'not_available','amountZat':300_000 if row else 0}

    def start(self):
        def loop():
            while not self.stopped.is_set():
                try:self.process_one()
                except Exception:pass  # Detailed native errors stay out of service logs.
                self.wake.wait(15);self.wake.clear()
        self.thread=threading.Thread(target=loop,name='welcome-grants',daemon=True);self.thread.start()

    def stop(self):
        self.stopped.set();self.wake.set()
        if self.thread:self.thread.join(timeout=340)
