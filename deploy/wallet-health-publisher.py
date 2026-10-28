#!/usr/bin/env python3
"""Publish public sync metadata only. Never a generic wallet RPC proxy."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import signal
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.request

NODE = Path('/var/lib/zec-tip-bot-chain-testnet/rpc')
def wallet_directory(backend):
    choices = {'zaino': Path('/var/lib/zec-tip-bot-wallet-testnet'),
               'zebra': Path('/var/lib/zec-tip-bot-wallet-nu7-testnet')}
    if backend not in choices:
        raise ValueError('Unknown prepared Testnet wallet backend.')
    return choices[backend]


BACKEND = os.environ.get('ZEC_WALLET_HEALTH_BACKEND', 'zaino')
WALLET = wallet_directory(BACKEND)
OUT = Path('/var/lib/zec-tip-bot-wallet-health/status.json')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def cookie(directory):
    candidates = []
    for pattern in ('*', '*/*'):
        for path in directory.glob(pattern):
            if path.is_file() and not path.is_symlink() and 'cookie' in path.name.lower():
                if directory.resolve() not in path.resolve().parents:
                    raise ValueError('Invalid cookie location.')
                candidates.append(path)
    if len(candidates) != 1:
        raise ValueError('Local cookie not ready.')
    credential = candidates[0].read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_]+:[!-~]{1,4096}', credential):
        raise ValueError('Invalid cookie format.')
    return credential


def rpc(method):
    # No caller can supply a URL, account, transaction, signing method or parameters.
    if method == 'getblockchaininfo':
        port, directory, timeout = 18232, NODE, 4
    elif method in ('rpc.discover', 'getwalletstatus'):
        port, directory, timeout = 28232, WALLET, 20 if method == 'getwalletstatus' else 4
    else:
        raise ValueError('This publisher only reads synchronization metadata.')
    body = json.dumps({'jsonrpc': '2.0', 'id': 'sync-health', 'method': method, 'params': []}).encode()
    req = urllib.request.Request(f'http://127.0.0.1:{port}/', data=body, headers={
        'Content-Type': 'application/json',
        'Authorization': 'Basic ' + base64.b64encode(cookie(directory).encode()).decode(),
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=timeout) as response:
        raw = response.read(4_000_001)
    if len(raw) > 4_000_000:
        raise ValueError('Read response too large.')
    payload = json.loads(raw)
    if payload.get('id') != 'sync-health' or payload.get('error'):
        raise ValueError('Local status read was rejected.')
    return payload.get('result')


def height(value):
    return type(value) is int and 0 <= value <= 0xffff_ffff


def observe(reader=rpc, now=None):
    result = {'version': 1, 'observedAt': int(time.time() * 1000) if now is None else now,
              'network': 'testnet', 'nodeRpc': False, 'walletRpc': False, 'walletStatus': 'unavailable'}
    try:
        node = reader('getblockchaininfo')
        if not isinstance(node, dict) or node.get('chain') not in ('test', 'testnet'):
            return result
        result['nodeRpc'] = True
        if height(node.get('blocks')):
            result['nodeHeight'] = node['blocks']
        progress = node.get('verificationprogress')
        if type(progress) in (int, float) and 0 <= progress <= 1:
            result['nodeProgress'] = progress
    except (ValueError, OSError, TimeoutError):
        return result
    try:
        discovery = reader('rpc.discover')
        if not isinstance(discovery, dict) or not isinstance(discovery.get('methods'), list):
            return result
        result['walletRpc'] = True
    except (ValueError, OSError, TimeoutError):
        return result
    try:
        status = reader('getwalletstatus')
        if not isinstance(status, dict):
            return result
        result['walletStatus'] = 'available'
        if isinstance(status.get('locked'), bool):
            result['walletLocked'] = status['locked']
        for key, output in (('node_tip', 'walletNodeHeight'), ('wallet_tip', 'walletHeight')):
            tip = status.get(key)
            if isinstance(tip, dict) and height(tip.get('height')):
                result[output] = tip['height']
    except (ValueError, OSError, TimeoutError) as exc:
        if isinstance(exc, TimeoutError) or isinstance(getattr(exc, 'reason', None), TimeoutError):
            result['walletStatus'] = 'timeout'
    return result


def publish(observation):
    # Atomic replacement preserves a complete last observation; the web container mounts this directory read-only.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=OUT.parent, prefix='.sync-', delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o644)
            json.dump(observation, stream, separators=(',', ':'), allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, OUT)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    cfg = tomllib.loads((WALLET / 'zallet.toml').read_text())
    if cfg.get('consensus', {}).get('network') != 'test' or cfg.get('backend') != BACKEND:
        raise ValueError('Publisher requires the prepared Testnet wallet.')
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        observation = observe()
        publish(observation)
        if args.once:
            print(json.dumps(observation, indent=2))
            return
        stop.wait(30)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError):
        raise SystemExit('Sync publisher stopped. No credentials or response bodies were printed.')
