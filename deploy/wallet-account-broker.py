#!/usr/bin/env python3
"""Private Testnet account provisioning. No signing, broadcast or key export endpoints."""
import argparse
import base64
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import socketserver
import sqlite3
import stat
import threading
import time
import urllib.request

SOCKET = Path('/run/zec-tip-bot-wallet-broker/wallet.sock')
CONFIG = Path('/etc/zec-tip-bot-wallet-broker.json')
DATA = Path('/var/lib/zec-tip-bot-wallet-broker')
WALLET = Path('/var/lib/zec-tip-bot-wallet-nu7-testnet')
NODE = Path('/var/lib/zec-tip-bot-chain-testnet/rpc')
UUID = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}')
LABEL = re.compile(r'tip-[a-f0-9]{32}')
SHIELDED_INDEX = 4294967296


class BrokerError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('Wallet account service could not complete this operation.')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class LocalRpc:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, method, params=None):
        node = method in ('getblockchaininfo', 'getblockheader')
        allowed = {'getblockchaininfo', 'getblockheader', 'getwalletstatus', 'z_listaccounts',
                   'z_getnewaccount', 'z_getaddressforaccount', 'z_getbalanceforaccount'}
        if method not in allowed:
            raise BrokerError('method_not_allowed')
        root = NODE if node else WALLET
        cookies = [p for pattern in ('*', '*/*') for p in root.glob(pattern)
                   if p.is_file() and not p.is_symlink() and 'cookie' in p.name.lower()]
        if len(cookies) != 1:
            raise BrokerError('wallet_unavailable')
        credential = cookies[0].read_text().strip()
        if not re.fullmatch(r'[A-Za-z0-9_]+:[!-~]{1,4096}', credential):
            raise BrokerError('wallet_unavailable')
        payload = {'jsonrpc': '2.0', 'id': 'account-broker', 'method': method, 'params': params or []}
        request = urllib.request.Request('http://127.0.0.1:' + ('18232' if node else '28232') + '/',
            data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json',
            'Authorization': 'Basic ' + base64.b64encode(credential.encode()).decode()})
        try:
            with self.opener.open(request, timeout=30 if method=='z_getnewaccount' else 4) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise BrokerError('invalid_wallet_response')
            result = json.loads(raw)
            if result.get('id') != 'account-broker' or result.get('error') or 'result' not in result:
                raise BrokerError('wallet_unavailable')
            return result['result']
        except BrokerError:
            raise
        except Exception:
            raise BrokerError('wallet_unavailable') from None


def native_backup_confirmed(fingerprint):
    with closing(sqlite3.connect((WALLET / 'wallet.db').as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        rows = db.execute('SELECT hex(hd_seed_fingerprint), backup_confirmed '
                          'FROM ext_zallet_keystore_mnemonics').fetchall()
    return len(rows) == 1 and rows[0] == (fingerprint.upper(), 1)


def chain_is_current(info, header, now):
    if not isinstance(info, dict) or not isinstance(header, dict):
        return False
    upgrades=info.get('upgrades')
    if not isinstance(upgrades,dict) or not isinstance(upgrades.get('77190ad9'),dict):
        return False
    nu7 = upgrades['77190ad9']
    return (info.get('chain') == 'test' and type(info.get('blocks')) is int and info['blocks'] >= 4465026
        and nu7.get('activationheight') == 4465026 and nu7.get('status') == 'active'
        and type(info.get('verificationprogress')) in (int, float) and info['verificationprogress'] >= 0.9999
        and type(header.get('time')) is int and -120 <= now - header['time'] <= 1800
        and header.get('hash') == info.get('bestblockhash') and header.get('height') == info['blocks'])


class Accounts:
    def __init__(self, config, database, rpc=None, backup_check=native_backup_confirmed):
        self.config, self.rpc, self.backup_check = config, rpc or LocalRpc(), backup_check
        self.db = sqlite3.connect(database, check_same_thread=False, isolation_level=None, timeout=5)
        self.db.execute('CREATE TABLE IF NOT EXISTS account_intents(label TEXT PRIMARY KEY,phase TEXT NOT NULL,account TEXT UNIQUE)')
        self.lock = threading.RLock()

    def close(self):
        self.db.close()

    def accounts(self):
        raw = self.rpc.call('z_listaccounts', [False])
        if not isinstance(raw, list):
            raise BrokerError('invalid_wallet_response')
        result = []
        for row in raw:
            if not isinstance(row, dict) or not isinstance(row.get('account_uuid'), str) or not UUID.fullmatch(row['account_uuid']):
                raise BrokerError('invalid_wallet_response')
            name = row.get('name', '')
            if not isinstance(name, str):
                name = ''
            # The broker never returns seed fingerprints, indices or address lists.
            result.append({'account': row['account_uuid'], 'name': name})
        return result

    def health(self):
        phase='node_info'
        try:
            info = self.rpc.call('getblockchaininfo')
            best = info.get('bestblockhash') if isinstance(info, dict) else None
            if not isinstance(best, str) or not re.fullmatch(r'[a-f0-9]{64}', best):
                raise BrokerError('node_not_ready')
            phase='node_header'
            header = self.rpc.call('getblockheader', [best, True])
            current = chain_is_current(info, header, time.time())
            phase='wallet_status'
            status = self.rpc.call('getwalletstatus')
            phase='wallet_accounts'
            accounts = self.accounts()
            node = status.get('node_tip', {}).get('height')
            wallet = status.get('wallet_tip', {}).get('height')
            views_current = (type(node) is int and type(wallet) is int
                and abs(info['blocks'] - node) <= 100 and abs(node - wallet) <= 100)
            phase='backup_confirmation'
            backed_up = self.backup_check(self.config['seedFingerprintHex'])
            recovered = self.config.get('localSeedRecoveryVerified') is True
            synced = current and views_current and status.get('locked') is False
            # An EMPTY wallet has no block_fully_scanned, hence locked=True. Native
            # account creation supports this bootstrap, but spending never inherits it.
            bootstrap = (not accounts and current and views_current and status.get('locked') is True
                         and status.get('fully_synced_height') is None)
            reason=('chain_not_current' if not current else 'wallet_view_not_ready' if not views_current
                    else 'recovery_not_confirmed' if not (backed_up and recovered)
                    else 'ready' if synced or bootstrap else 'wallet_syncing')
            return {'network': 'testnet', 'synced': synced, 'currentConsensusVerified': current,
                    'backupConfirmed': backed_up and recovered,
                    'accountCreationReady': backed_up and recovered and (synced or bootstrap),
                    'emptyWalletBootstrap': bootstrap, 'depositsEnabled': False, 'transfersEnabled': False,
                    'readinessReason': reason}
        except Exception as error:
            return {'network': 'testnet', 'synced': False, 'currentConsensusVerified': False,
                    'backupConfirmed': False, 'accountCreationReady': False, 'emptyWalletBootstrap': False,
                    'depositsEnabled': False, 'transfersEnabled': False,
                    'readinessReason': 'backend_unavailable', 'diagnosticPhase':phase,'errorCategory':type(error).__name__}

    def create(self, label):
        if not isinstance(label, str) or not LABEL.fullmatch(label):
            raise BrokerError('invalid_request')
        with self.lock:
            accounts = self.accounts()
            matched = [row for row in accounts if row['name'] == label]
            if len(matched) > 1:
                raise BrokerError('account_review_required')
            if matched:
                account = matched[0]['account']
                self.db.execute('INSERT INTO account_intents VALUES (?,\'created\',?) ON CONFLICT(label) '
                                'DO UPDATE SET phase=excluded.phase,account=excluded.account', (label, account))
                return {'account': account}
            if not self.health()['accountCreationReady']:
                raise BrokerError('wallet_not_ready')
            if self.db.execute('SELECT 1 FROM account_intents WHERE label=?', (label,)).fetchone():
                raise BrokerError('account_review_required')
            self.db.execute("INSERT INTO account_intents VALUES (?,'creating',NULL)", (label,))
            try:
                raw = self.rpc.call('z_getnewaccount', [label])
                account = raw.get('account_uuid') if isinstance(raw, dict) else None
                if not isinstance(account, str) or not UUID.fullmatch(account):
                    raise BrokerError('invalid_wallet_response')
                self.db.execute("UPDATE account_intents SET phase='created',account=? WHERE label=?", (account, label))
                return {'account': account}
            except Exception:
                self.db.execute("UPDATE account_intents SET phase='uncertain' WHERE label=?", (label,))
                raise BrokerError('account_review_required') from None

    def address(self, account, index):
        if not isinstance(account, str) or not UUID.fullmatch(account) or type(index) is not int or index not in (0,SHIELDED_INDEX):
            raise BrokerError('invalid_request')
        with self.lock:
            if not self.db.execute("SELECT 1 FROM account_intents WHERE account=? AND phase='created'", (account,)).fetchone():
                raise BrokerError('account_not_bound')
            # Orchard-only avoids invalid Sapling diversifiers at index zero and
            # prevents the native default from adding a transparent receiver.
            raw = self.rpc.call('z_getaddressforaccount', [account, ['orchard'], index])
            if (not isinstance(raw, dict) or raw.get('account_uuid') != account
                    or type(raw.get('diversifier_index')) is not int or raw.get('diversifier_index') != index or raw.get('receiver_types') != ['orchard']
                    or not isinstance(raw.get('address'), str) or not raw['address'].startswith('utest1')
                    or len(raw['address']) > 2000):
                raise BrokerError('invalid_wallet_response')
            return {'network': 'testnet', 'account': account, 'address': raw['address']}

    def balance(self, account):
        if not isinstance(account,str) or not UUID.fullmatch(account):
            raise BrokerError('invalid_request')
        with self.lock:
            if not self.db.execute("SELECT 1 FROM account_intents WHERE account=? AND phase='created'",(account,)).fetchone():
                raise BrokerError('account_not_bound')
            if not self.health()['synced']:
                raise BrokerError('wallet_not_ready')
            raw=self.rpc.call('z_getbalanceforaccount',[account,10])
            return read_balance(raw,account)


def read_balance(raw,account):
    if (not isinstance(raw,dict) or type(raw.get('minimum_confirmations')) is not int
            or raw['minimum_confirmations']!=10 or not isinstance(raw.get('pools'),dict)
            or set(raw['pools'])-{'transparent','sapling','orchard','ironwood'}):
        raise BrokerError('invalid_wallet_response')
    values={}
    for pool,value in raw['pools'].items():
        amount=value.get('valueZat') if isinstance(value,dict) else None
        if type(amount) is not int or not 0 <= amount <= 2_100_000_000_000_000:
            raise BrokerError('invalid_wallet_response')
        values[pool]=amount
    shielded=sum(values.get(pool,0) for pool in ('sapling','orchard','ironwood'))
    if shielded>2_100_000_000_000_000: raise BrokerError('invalid_wallet_response')
    return {'network':'testnet','account':account,'confirmedSpendableZat':shielded,
            'minimumConfirmations':10,'transparentBalancePresent':values.get('transparent',0)>0}


if hasattr(socketserver, 'UnixStreamServer'):
    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True
        request_queue_size = 8
else:
    class Server:
        def __init__(self, *args):
            raise BrokerError('linux_unix_socket_required')


def handler(accounts):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'PrivateAccountBroker'
        def log_message(self, *args):
            pass  # Never log requests, addresses, native errors or account identifiers.

        def setup(self):
            super().setup()
            self.connection.settimeout(40)

        def response(self, status, result):
            raw = json.dumps(result).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            try:
                if self.path == '/health':
                    return self.response(200, accounts.health())
                if self.path == '/accounts':
                    return self.response(200, accounts.accounts())
                match=re.fullmatch(r'/balance/([a-f0-9-]{36})',self.path)
                if match:
                    return self.response(200,accounts.balance(match.group(1)))
                self.response(404, {'error': 'not_found'})
            except Exception:
                self.response(503, {'error': 'wallet_unavailable'})

        def do_POST(self):
            try:
                length = self.headers.get('Content-Length', '')
                if (not length.isdecimal() or not 1 <= int(length) <= 512
                        or self.headers.get('Transfer-Encoding') is not None
                        or self.headers.get('Content-Type') != 'application/json'):
                    raise BrokerError('invalid_request')
                document = json.loads(self.rfile.read(int(length)))
                if self.path == '/accounts' and isinstance(document, dict) and set(document) == {'label'}:
                    return self.response(200, accounts.create(document['label']))
                if self.path == '/address' and isinstance(document, dict) and set(document) == {'account', 'index'}:
                    return self.response(200, accounts.address(document['account'], document['index']))
                raise BrokerError('invalid_request')
            except BrokerError as error:
                self.response(409 if error.code != 'invalid_request' else 400, {'error': error.code})
            except Exception:
                self.response(503, {'error': 'wallet_unavailable'})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if os.name != 'posix' or not hasattr(socketserver, 'UnixStreamServer'):
        raise BrokerError('linux_unix_socket_required')
    info = CONFIG.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or info.st_size > 4096):
        raise BrokerError('invalid_configuration')
    config = json.loads(CONFIG.read_text())
    if (config.get('network') != 'testnet' or config.get('sourceRevision') != '8c5a93ba9d68ad78d199c3624413c0139781b837'
            or not isinstance(config.get('seedFingerprintHex'), str)
            or not re.fullmatch(r'[A-F0-9]{64}', config['seedFingerprintHex'])
            or config.get('localSeedRecoveryVerified') is not True
            or type(config.get('clientGroup')) is not int or not 100 <= config['clientGroup'] <= 65535):
        raise BrokerError('invalid_configuration')
    os.umask(0o077)
    accounts = Accounts(config, DATA / 'accounts.sqlite')
    try:
        if args.check:
            print(json.dumps(accounts.health(), indent=2))
            return
        if SOCKET.exists() or SOCKET.is_symlink():
            if not stat.S_ISSOCK(SOCKET.lstat().st_mode):
                raise BrokerError('socket_path_in_use')
            SOCKET.unlink()
        with Server(str(SOCKET), handler(accounts)) as server:
            os.chmod(SOCKET, 0o660)
            os.chown(SOCKET, -1, int(config['clientGroup']))
            print('Private Testnet account service listening. Transfers and deposits remain disabled.', flush=True)
            server.serve_forever(poll_interval=0.5)
    finally:
        accounts.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'accountBroker': 'stopped', 'error': error.code
                         if isinstance(error, BrokerError) else type(error).__name__}))
        raise SystemExit(1)
