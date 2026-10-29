#!/usr/bin/env python3
"""Testnet-only payment state machine. Native keys never leave the wallet daemon.

The web application receives an opaque proposal token, never an importable PCZT.
Signing and broadcasting require an explicit protected operator switch. Merely
installing this file cannot enable a transfer or change the running service.
"""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import threading
import time
import urllib.error
import urllib.request

spec=importlib.util.spec_from_file_location('account_broker',Path(__file__).with_name('wallet-account-broker.py'))
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
spec=importlib.util.spec_from_file_location('wallet_enrollment',Path(__file__).with_name('wallet-enrollment.py'))
enrollment_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(enrollment_module)
BrokerError=base.BrokerError
HEX=re.compile(r'[a-f0-9]{64}')
TOKEN=re.compile(r'[a-f0-9]{48}')
MAX_TIP=1_000_000  # 0.01 Testnet ZEC pilot ceiling, independent of the app.
MAX_FEE=100_000
DAILY=10_000_000
CONTROL=Path('/etc/zec-tip-bot-wallet-transfers.json')


class NativeError(BrokerError):
    def __init__(self,number):
        self.number=number
        super().__init__('native_rejected')


class NativeRpc:
    NODE={'getblockchaininfo','getblockheader','sendrawtransaction','getrawtransaction'}
    ALLOWED=NODE|{'getwalletstatus','z_listaccounts','z_listtransactions','z_getbalances','z_getnewaccount','z_getaddressforaccount',
                 'z_getbalanceforaccount','z_listunifiedreceivers','pczt_create','pczt_inspect',
                 'pczt_prove','pczt_sign','pczt_extract','decoderawtransaction'}
    def __init__(self):
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),base.NoRedirect())

    def call(self,method,params=None):
        if method not in self.ALLOWED:raise BrokerError('method_not_allowed')
        root=base.NODE if method in self.NODE else base.WALLET
        cookies=[p for pattern in ('*','*/*') for p in root.glob(pattern)
                 if p.is_file() and not p.is_symlink() and 'cookie' in p.name.lower()]
        if len(cookies)!=1:raise BrokerError('wallet_unavailable')
        credential=cookies[0].read_text().strip()
        if not re.fullmatch(r'[A-Za-z0-9_]+:[!-~]{1,4096}',credential):raise BrokerError('wallet_unavailable')
        document={'jsonrpc':'2.0','id':secrets.token_hex(12),'method':method,'params':params or []}
        request=urllib.request.Request('http://127.0.0.1:'+('18232' if method in self.NODE else '28232')+'/',
            data=json.dumps(document).encode(),headers={'Content-Type':'application/json',
            'Authorization':'Basic '+base64.b64encode(credential.encode()).decode()})
        try:
            try:response=self.opener.open(request,timeout=330 if method.startswith('pczt_') else 12)
            except urllib.error.HTTPError as error:
                # JSON-RPC may carry a numeric error in HTTP 500; never expose its body.
                if error.code not in (400,404,500):raise
                response=error
            with response:raw=response.read(8_000_001)
            if len(raw)>8_000_000:raise BrokerError('invalid_wallet_response')
            value=json.loads(raw)
            if not isinstance(value,dict) or value.get('id')!=document['id']:raise BrokerError('invalid_wallet_response')
            if value.get('error'):
                number=value['error'].get('code') if isinstance(value['error'],dict) else None
                if type(number) is int:raise NativeError(number)
                raise BrokerError('wallet_unavailable')
            if 'result' not in value:raise BrokerError('invalid_wallet_response')
            return value['result']
        except BrokerError:raise
        except Exception:raise BrokerError('wallet_unavailable') from None


def controls():
    if not CONTROL.exists():return {'enabled':False,'depositsEnabled':False}
    info=CONTROL.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or info.st_size>4096:
        raise BrokerError('invalid_configuration')
    result=json.loads(CONTROL.read_text())
    if (result.get('network')!='testnet' or type(result.get('enabled')) is not bool
            or type(result.get('depositsEnabled')) is not bool
            or result.get('bindingBackupVerified') is not True
            or (result.get('minimumConfirmations',10) not in (3,10) or type(result.get('minimumConfirmations',10)) is not int)
            or not isinstance(result.get('accountsSha256'),str) or not HEX.fullmatch(result['accountsSha256'])):raise BrokerError('invalid_configuration')
    return result


def integer(value,minimum=0,maximum=2_100_000_000_000_000):
    return type(value) is int and minimum<=value<=maximum


def pczt(result):
    value=result.get('pczt') if isinstance(result,dict) else None
    if not isinstance(value,str) or not 1<=len(value)<=4_000_000:raise BrokerError('invalid_wallet_response')
    try:base64.b64decode(value,validate=True)
    except Exception:raise BrokerError('invalid_wallet_response') from None
    return value


def inspect_terms(value,request,identity,height):
    """Check claims of an internally-created PCZT, not arbitrary imported material.

    The native extractor subsequently verifies shielded proofs and signatures.
    Only the native wallet creates proposals accepted by this broker.
    """
    if (not isinstance(value,dict) or value.get('wallet_created') is not True
            or value.get('privacy_policy')!='FullPrivacy' or value.get('consensus_branch_id')!='77190ad9'
            or value.get('tx_version')!=6 or not integer(value.get('expiry_height'),height+10,height+1000)
            or not integer(value.get('fee_zat'),0,request['maxFeeZat'])
            or value.get('signing_hints')!={'seed_fingerprint':identity['seedfp'],'account_index':identity['zip32_account_index']}
            or value.get('transparent')!={'inputs':[],'outputs':[]}):raise BrokerError('proposal_rejected')
    financial=[];external=[]
    for name in ('sapling','orchard','ironwood'):
        bundle=value.get(name)
        if not isinstance(bundle,dict) or not isinstance(bundle.get('outputs'),list) or len(bundle['outputs'])>100:
            raise BrokerError('proposal_rejected')
        for output in bundle['outputs']:
            if not isinstance(output,dict) or not integer(output.get('value_zat')):raise BrokerError('proposal_rejected')
            if output.get('user_address') is not None:external.append((output['user_address'],output['value_zat']))
        financial.append({'pool':name,'outputs':bundle['outputs'],'balance':bundle.get('value_balance_zat')})
    if external!=[(request['recipient'],request['amountZat'])]:raise BrokerError('proposal_rejected')
    return {'version':value['tx_version'],'branch':value['consensus_branch_id'],'expiry':value['expiry_height'],
            'fee':value['fee_zat'],'identity':value['signing_hints'],'bundles':financial}


class Transfers(base.Accounts):
    def __init__(self,config,database,rpc=None,backup_check=base.native_backup_confirmed,control=controls):
        super().__init__(config,database,rpc or NativeRpc(),backup_check)
        self.control=control
        self.spend_lock=threading.Lock()
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS transfer_jobs(
            request_key TEXT PRIMARY KEY,token TEXT NOT NULL UNIQUE,request TEXT NOT NULL,
            phase TEXT NOT NULL,pczt TEXT,terms TEXT,signed TEXT,day TEXT NOT NULL,reservation INTEGER NOT NULL)''')
        self.enrollment=enrollment_module.Enrollment(self)

    def health(self):
        result=super().health()
        try:control=self.control()
        except Exception:control={}
        with self.lock:
            accounts=[row[0] for row in self.db.execute("SELECT account FROM account_intents WHERE phase='created' ORDER BY account")]
        digest=hashlib.sha256('\n'.join(accounts).encode()).hexdigest()
        coverage=control.get('accountsSha256')==digest
        options=None
        try:
            options=enrollment_module.policy(control)
            if options:
                baseline=options['baselineAccounts']
                active=[r[0] for r in self.db.execute("SELECT account FROM enrollment_bindings WHERE phase='active'")]
                coverage=(set(baseline)<=set(accounts) and all(self.enrollment.admitted(a,options) for a in active))
        except Exception:coverage=False
        ready=(result['synced'] and result['backupConfirmed'] and result['currentConsensusVerified'] and coverage)
        result.update(depositsEnabled=ready and control.get('depositsEnabled') is True,
                      transfersEnabled=ready and control.get('enabled') is True)
        # Once the pilot account set is backed up, new enrollment waits for an
        # operator-controlled backup window instead of invalidating funded users.
        if control.get('bindingBackupVerified') is True:
            result['accountCreationReady']=bool(options and options['enabled'] and coverage and result['accountCreationReady'] and len(accounts)<base.MAX_ACCOUNTS)
        result['accountAdmissionRequired']=options is not None
        result['receivingIndex']=base.SHIELDED_INDEX if options else 0
        result['minimumConfirmations']=self.minimum_confirmations()
        return result

    def minimum_confirmations(self):
        value=self.control().get('minimumConfirmations',10)
        if type(value) is not int or value not in (3,10):raise BrokerError('invalid_configuration')
        return value

    def admit(self,value):
        try:return self.enrollment.admit(value)
        except enrollment_module.EnrollmentError:raise BrokerError('enrollment_not_ready') from None

    def balance(self,account):
        if not self.enrollment.admitted(account):raise BrokerError('account_not_admitted')
        self.identity(account)
        if not self.health()['synced']:raise BrokerError('wallet_not_ready')
        minimum=self.minimum_confirmations()
        return read_account_balance(self.rpc.call('z_getbalances',[minimum]),account,minimum)

    def ready(self):
        health=self.health()
        if not health['transfersEnabled']:raise BrokerError('transfers_disabled')

    def receipts(self,account):
        self.identity(account)
        if not self.health()['synced']:raise BrokerError('wallet_not_ready')
        height=self.rpc.call('getblockchaininfo')['blocks']
        with self.lock:
            known={json.loads(row[0])['txid'] for row in self.db.execute('SELECT t.signed FROM transfer_jobs t LEFT JOIN welcome_grants w ON w.request_key=t.request_key WHERE t.signed IS NOT NULL AND w.request_key IS NULL')}
        raw=self.rpc.call('z_listtransactions',[account,None,None,0,30])
        return {'network':'testnet','account':account,'receipts':read_receipts(raw,account,height,known)}

    def row(self,key=None,token=None,txid=None):
        with self.lock:
            if key is not None:row=self.db.execute('SELECT * FROM transfer_jobs WHERE request_key=?',(key,)).fetchone()
            elif token is not None:row=self.db.execute('SELECT * FROM transfer_jobs WHERE token=?',(token,)).fetchone()
            else:
                rows=self.db.execute("SELECT * FROM transfer_jobs WHERE phase IN ('signed','broadcasting','submitted')").fetchall()
                row=next((r for r in rows if json.loads(r[6])['txid']==txid),None)
            return dict(zip(('key','token','request','phase','pczt','terms','signed','day','reservation'),row)) if row else None

    def update(self,token,phase,**values):
        if set(values)-{'pczt','terms','signed'}:raise BrokerError('invalid_request')
        with self.lock:
            self.db.execute('UPDATE transfer_jobs SET phase=?'+''.join(','+name+'=?' for name in values)+' WHERE token=?',
                            [phase,*values.values(),token])

    def claim_signing(self,token):
        with self.lock:
            return self.db.execute("UPDATE transfer_jobs SET phase='proving' WHERE token=? AND phase='prepared'",(token,)).rowcount==1

    def identity(self,account):
        if not self.enrollment.admitted(account):raise BrokerError('account_not_admitted')
        with self.lock:
            bound=self.db.execute("SELECT 1 FROM account_intents WHERE account=? AND phase='created'",(account,)).fetchone()
        if not bound:raise BrokerError('account_not_bound')
        rows=self.rpc.call('z_listaccounts',[False])
        found=[r for r in rows if isinstance(r,dict) and r.get('account_uuid')==account] if isinstance(rows,list) else []
        if (len(found)!=1 or not isinstance(found[0].get('seedfp'),str)
                or not integer(found[0].get('zip32_account_index'),0,0x7fffffff)):raise BrokerError('account_not_bound')
        return found[0]

    def validate_request(self,value):
        if (not isinstance(value,dict) or set(value)!={'idempotencyKey','account','recipient','amountZat','maxFeeZat'}
                or not isinstance(value['idempotencyKey'],str) or not HEX.fullmatch(value['idempotencyKey'])
                or not isinstance(value['account'],str) or not base.UUID.fullmatch(value['account'])
                or not isinstance(value['recipient'],str) or not re.fullmatch(r'utest1[0-9a-z]{20,1994}',value['recipient'])
                or not integer(value['amountZat'],1,MAX_TIP) or not integer(value['maxFeeZat'],1,MAX_FEE)):
            raise BrokerError('invalid_request')
        receivers=self.rpc.call('z_listunifiedreceivers',[value['recipient']])
        if (not isinstance(receivers,dict) or set(receivers)-{'orchard','sapling'}
                or not receivers or any(not isinstance(r,str) for r in receivers.values())):raise BrokerError('shielded_testnet_required')

    def proposal(self,row):
        request=json.loads(row['request']);terms=json.loads(row['terms'])
        return {'network':'testnet','account':request['account'],'recipient':request['recipient'],
                'amountZat':request['amountZat'],'feeZat':terms['fee'],'privacy':'FullPrivacy',
                'transparentInputs':0,'transparentOutputs':0,'proposal':row['token']}

    def prepare(self,value):
        if not self.spend_lock.acquire(blocking=False):raise BrokerError('wallet_busy')
        token=None
        try:
            self.ready();self.validate_request(value)
            canonical=json.dumps(value,sort_keys=True,separators=(',',':'))
            old=self.row(key=value['idempotencyKey'])
            if old:
                if old['request']!=canonical:raise BrokerError('idempotency_conflict')
                if old['phase'] in ('prepared','signed','broadcasting','submitted'):return self.proposal(old)
                if old['phase']=='rejected':raise BrokerError('proven_rejection')
                raise BrokerError('payment_review_required')
            identity=self.identity(value['account'])
            day=time.strftime('%Y-%m-%d',time.gmtime())
            with self.lock:
                self.db.execute('BEGIN IMMEDIATE')
                try:
                    if self.db.execute("SELECT 1 FROM transfer_jobs WHERE phase NOT IN ('submitted','rejected')").fetchone():
                        raise BrokerError('payment_review_required')
                    used=self.db.execute("SELECT COALESCE(SUM(reservation),0) FROM transfer_jobs WHERE day=? AND phase!='rejected'",(day,)).fetchone()[0]
                    if used+value['amountZat']+value['maxFeeZat']>DAILY:raise BrokerError('pilot_daily_limit')
                    token=secrets.token_hex(24)
                    self.db.execute("INSERT INTO transfer_jobs VALUES (?,?,?,'preparing',NULL,NULL,NULL,?,?)",
                                    (value['idempotencyKey'],token,canonical,day,value['amountZat']+value['maxFeeZat']))
                    self.db.execute('COMMIT')
                except Exception:self.db.execute('ROLLBACK');raise
            amount=f"{value['amountZat']//100_000_000}.{value['amountZat']%100_000_000:08d}"
            try:
                result=self.rpc.call('pczt_create',[value['account'],[{'address':value['recipient'],'amount':amount}],self.minimum_confirmations(),'FullPrivacy'])
            except NativeError as error:
                # Only an explicit insufficient-funds rejection is definitely unsent.
                if error.number==-6:self.update(token,'rejected');token=None;raise BrokerError('proven_rejection') from None
                raise
            created=pczt(result)
            self.update(token,'preparing',pczt=created)
            if result.get('privacy_policy')!='FullPrivacy':raise BrokerError('proposal_rejected')
            height=self.rpc.call('getblockchaininfo')['blocks']
            inspected=self.rpc.call('pczt_inspect',[created])
            # Validate all other terms first. A fee-only refusal is definitely
            # before proving/signing and must not block later authorized jobs.
            inspect_terms(inspected,{**value,'maxFeeZat':2_100_000_000_000_000},identity,height)
            if inspected['fee_zat']>value['maxFeeZat']:
                self.update(token,'rejected');token=None
                raise BrokerError('proven_fee_rejection')
            terms=inspect_terms(inspected,value,identity,height)
            self.update(token,'prepared',pczt=created,terms=json.dumps(terms,sort_keys=True))
            answer=self.proposal(self.row(token=token));token=None;return answer
        except Exception:
            if token:self.update(token,'review')
            raise
        finally:self.spend_lock.release()

    def check_terms(self,row,material):
        request=json.loads(row['request']);identity=self.identity(request['account'])
        height=self.rpc.call('getblockchaininfo')['blocks']
        value=self.rpc.call('pczt_inspect',[material])
        terms=inspect_terms(value,request,identity,height)
        if terms!=json.loads(row['terms']):raise BrokerError('proposal_changed')
        return value

    def sign(self,value):
        if (not isinstance(value,dict) or set(value)!={'idempotencyKey','proposal'}
                or not isinstance(value['idempotencyKey'],str) or not HEX.fullmatch(value['idempotencyKey'])
                or not isinstance(value['proposal'],str) or not TOKEN.fullmatch(value['proposal'])):raise BrokerError('invalid_request')
        if not self.spend_lock.acquire(blocking=False):raise BrokerError('wallet_busy')
        token=None
        try:
            self.ready();row=self.row(key=value['idempotencyKey'])
            if not row or row['token']!=value['proposal']:raise BrokerError('payment_not_found')
            if row['phase'] in ('signed','broadcasting','submitted'):return json.loads(row['signed'])
            if row['phase']!='prepared':raise BrokerError('payment_review_required')
            self.check_terms(row,row['pczt'])
            if not self.claim_signing(row['token']):raise BrokerError('payment_review_required')
            token=row['token']
            proven=pczt(self.rpc.call('pczt_prove',[row['pczt']]))
            inspected=self.check_terms(row,proven)
            if (inspected['sapling'].get('proofs_complete') is not True
                    or any(inspected[pool].get('proof_complete') is not True for pool in ('orchard','ironwood'))):raise BrokerError('proof_incomplete')
            self.ready();self.update(token,'signing',pczt=proven)
            signed=self.rpc.call('pczt_sign',[proven,'FullPrivacy',True]);material=pczt(signed)
            if any(signed.get('unsigned_'+pool)!=[] for pool in ('transparent','sapling','orchard','ironwood')):
                raise BrokerError('signature_incomplete')
            self.check_terms(row,material)
            self.ready();self.update(token,'extracting',pczt=material)
            extracted=self.rpc.call('pczt_extract',[material])
            raw=extracted.get('hex') if isinstance(extracted,dict) else None
            txid=extracted.get('txid') if isinstance(extracted,dict) else None
            if (not isinstance(extracted,dict) or extracted.get('stored') is not True or not isinstance(raw,str) or not 2<=len(raw)<=4_000_000 or len(raw)%2 or not re.fullmatch(r'[a-f0-9]+',raw)
                    or not isinstance(txid,str) or not HEX.fullmatch(txid)):raise BrokerError('invalid_wallet_response')
            # ZIP-244 IDs are not the double-SHA256 of the serialized transaction.
            decoded=self.rpc.call('decoderawtransaction',[raw])
            if (not isinstance(decoded,dict) or decoded.get('txid')!=txid or decoded.get('version')!=6
                    or decoded.get('vin')!=[] or decoded.get('vout')!=[]
                    or decoded.get('expiryheight')!=json.loads(row['terms'])['expiry']):raise BrokerError('transaction_mismatch')
            answer={'txid':txid,'raw':raw}
            self.update(token,'signed',signed=json.dumps(answer));token=None;return answer
        except Exception:
            if token:self.update(token,'review')
            raise
        finally:self.spend_lock.release()

    def known_transaction(self,txid):
        try:result=self.rpc.call('getrawtransaction',[txid,1])
        except NativeError as error:
            if error.number==-5:return None
            raise
        if not isinstance(result,dict) or result.get('txid')!=txid:raise BrokerError('invalid_wallet_response')
        confirmations=result.get('confirmations',0)
        if not integer(confirmations,0,10_000_000):raise BrokerError('invalid_wallet_response')
        return confirmations

    def broadcast(self,value):
        if (not isinstance(value,dict) or set(value)!={'txid','raw'} or not isinstance(value['txid'],str)
                or not HEX.fullmatch(value['txid']) or not isinstance(value['raw'],str)):raise BrokerError('invalid_request')
        if not self.spend_lock.acquire(blocking=False):raise BrokerError('wallet_busy')
        try:
            self.ready();row=self.row(txid=value['txid'])
            if not row or json.loads(row['signed'])!=value:raise BrokerError('transaction_not_bound')
            if self.known_transaction(value['txid']) is None:
                height=self.rpc.call('getblockchaininfo')['blocks']
                if height>=json.loads(row['terms'])['expiry']:raise BrokerError('transaction_expired')
                self.update(row['token'],'broadcasting')
                # On error leave these exact bytes durable. Never create another spend.
                try:txid=self.rpc.call('sendrawtransaction',[value['raw']])
                except Exception:
                    if self.known_transaction(value['txid']) is None:raise
                    txid=value['txid']
                if txid!=value['txid']:raise BrokerError('transaction_mismatch')
            self.update(row['token'],'submitted')
            return {'txid':value['txid']}
        finally:self.spend_lock.release()

    def confirmations(self,txid):
        if not isinstance(txid,str) or not HEX.fullmatch(txid) or not self.row(txid=txid):raise BrokerError('transaction_not_bound')
        result=self.known_transaction(txid)
        return {'confirmations':result if result is not None else 0}


def read_account_balance(raw,account,minimum=10):
    """Account-filtered native categories. Pending/dust never become spendable."""
    if (type(minimum) is not int or minimum not in (3,10) or not isinstance(account,str) or not base.UUID.fullmatch(account) or not isinstance(raw,dict)
            or not isinstance(raw.get('accounts'),list) or len(raw['accounts'])>100):raise BrokerError('invalid_wallet_response')
    seen=set();selected=None
    for row in raw['accounts']:
        if not isinstance(row,dict) or not isinstance(row.get('account_uuid'),str) or not base.UUID.fullmatch(row['account_uuid']) or row['account_uuid'] in seen:
            raise BrokerError('invalid_wallet_response')
        seen.add(row['account_uuid'])
        if row['account_uuid']==account:selected=row
    if selected is None:raise BrokerError('invalid_wallet_response')
    if set(selected)-{'account_uuid','transparent','transparent_watchonly','sapling','orchard','ironwood','total'}:raise BrokerError('invalid_wallet_response')
    totals={'spendable':0,'pending':0,'dust':0}
    for pool in ('sapling','orchard','ironwood'):
        bundle=selected.get(pool)
        if bundle is None:continue
        if not isinstance(bundle,dict) or 'spendable' not in bundle:raise BrokerError('invalid_wallet_response')
        for category in ('spendable','pending','dust','locked'):
            item=bundle.get(category)
            if item is None:
                if category=='spendable':raise BrokerError('invalid_wallet_response')
                continue
            if not isinstance(item,dict) or not integer(item.get('valueZat')):raise BrokerError('invalid_wallet_response')
            if category in totals:totals[category]+=item['valueZat']
    if not integer(sum(totals.values())):raise BrokerError('invalid_wallet_response')
    return {'network':'testnet','account':account,'confirmedSpendableZat':totals['spendable'],
            'pendingZat':totals['pending'],'uneconomicZat':totals['dust'],'minimumConfirmations':minimum,
            'transparentBalancePresent':selected.get('transparent') is not None or bool(selected.get('transparent_watchonly'))}


def read_receipts(raw,account,height,known):
    # Never forward native memos, addresses, account labels or raw transaction IDs.
    if not base.UUID.fullmatch(account) or type(height) is not int or height<0 or not isinstance(raw,list) or len(raw)>30:
        raise BrokerError('invalid_wallet_response')
    result=[];seen=set()
    for row in raw:
        if not isinstance(row,dict) or row.get('account_uuid')!=account:raise BrokerError('invalid_wallet_response')
        txid=row.get('txid')
        if not isinstance(txid,str) or not HEX.fullmatch(txid) or txid in seen:raise BrokerError('invalid_wallet_response')
        seen.add(txid)
        if row.get('expired_unmined') is not False:continue
        delta=row.get('account_balance_delta')
        if type(delta) is not int:raise BrokerError('invalid_wallet_response')
        if txid in known or delta<=0:continue
        outputs=row.get('outputs')
        if not isinstance(outputs,list):raise BrokerError('invalid_wallet_response')
        incoming=[]
        for output in outputs:
            if not isinstance(output,dict):raise BrokerError('invalid_wallet_response')
            if output.get('to_account')!=account or output.get('is_change') is not False:continue
            if output.get('pool') not in ('sapling','orchard','ironwood'):continue
            value=output.get('value')
            if type(value) is not int or not 0<value<=2_100_000_000_000_000:raise BrokerError('invalid_wallet_response')
            incoming.append(value)
        if not incoming:continue
        amount=sum(incoming)
        if not 0<amount<=2_100_000_000_000_000:raise BrokerError('invalid_wallet_response')
        mined=row.get('mined_height');timestamp=row.get('block_time')
        if mined is not None and (type(mined) is not int or not 0<=mined<=height):raise BrokerError('invalid_wallet_response')
        if timestamp is not None and (type(timestamp) is not int or not 0<=timestamp<=4_102_444_800):raise BrokerError('invalid_wallet_response')
        result.append({'receipt':hashlib.sha256(('zec-tip-bot:receipt:'+txid).encode()).hexdigest(),
                       'amountZat':amount,'confirmations':height-mined+1 if mined is not None else 0,
                       'time':timestamp})
    return result


def handler(service):
    parent=base.handler(service)
    class Handler(parent):
        def setup(self):
            super().setup();self.connection.settimeout(360)

        def do_GET(self):
            welcome=re.fullmatch(r'/welcome/([a-f0-9-]{36})',self.path)
            if welcome:
                try:self.response(200,service.enrollment.status(welcome.group(1)))
                except Exception:self.response(503,{'error':'wallet_unavailable'})
                return
            receipts=re.fullmatch(r'/receipts/([a-f0-9-]{36})',self.path)
            if receipts:
                try:self.response(200,service.receipts(receipts.group(1)))
                except Exception:self.response(503,{'error':'wallet_unavailable'})
                return
            match=re.fullmatch(r'/confirmations/([a-f0-9]{64})',self.path)
            if not match:return super().do_GET()
            try:self.response(200,service.confirmations(match.group(1)))
            except Exception:self.response(503,{'error':'wallet_unavailable'})

        def do_POST(self):
            functions={'/prepare':service.prepare,'/sign':service.sign,'/broadcast':service.broadcast,'/admit':service.admit}
            if self.path not in functions:return super().do_POST()
            try:
                length=self.headers.get('Content-Length','')
                if (not length.isdecimal() or not 1<=int(length)<=4_100_000
                        or self.headers.get('Transfer-Encoding') is not None
                        or self.headers.get('Content-Type')!='application/json'):raise BrokerError('invalid_request')
                document=json.loads(self.rfile.read(int(length)))
                self.response(200,functions[self.path](document))
            except BrokerError as error:
                # Fixed public codes only. No native message, PCZT, address or amount.
                code=error.code if not isinstance(error,NativeError) else 'wallet_unavailable'
                self.response(409,{'error':code})
            except Exception:self.response(503,{'error':'wallet_unavailable'})
    return Handler


def main():
    # Reuse the installed account service's protected config, socket and identity isolation.
    info=base.CONFIG.lstat()
    if os.name!='posix' or not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022 or info.st_size>4096:
        raise BrokerError('invalid_configuration')
    config=json.loads(base.CONFIG.read_text())
    if (config.get('network')!='testnet' or config.get('sourceRevision')!='8c5a93ba9d68ad78d199c3624413c0139781b837'
            or not isinstance(config.get('seedFingerprintHex'),str) or not re.fullmatch(r'[A-F0-9]{64}',config['seedFingerprintHex'])
            or config.get('localSeedRecoveryVerified') is not True or not integer(config.get('clientGroup'),100,65535)):
        raise BrokerError('invalid_configuration')
    os.umask(0o077);service=Transfers(config,base.DATA/'accounts.sqlite')
    try:
        controls()
        service.enrollment.start()
        if base.SOCKET.exists() or base.SOCKET.is_symlink():
            if not stat.S_ISSOCK(base.SOCKET.lstat().st_mode):raise BrokerError('socket_path_in_use')
            base.SOCKET.unlink()
        with base.Server(str(base.SOCKET),handler(service)) as server:
            os.chmod(base.SOCKET,0o660);os.chown(base.SOCKET,-1,config['clientGroup'])
            print('Private Testnet wallet service listening. Operator controls determine availability.',flush=True)
            server.serve_forever(poll_interval=0.5)
    finally:service.enrollment.stop();service.close()


if __name__=='__main__':
    try:main()
    except Exception:print('Private Testnet wallet service stopped. Inspect its protected configuration.');raise SystemExit(1)
