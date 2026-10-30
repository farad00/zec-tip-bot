#!/usr/bin/env python3
"""Privately add user OAuth2 client credentials to the existing VPS environment."""
import getpass
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

ENV = Path('/srv/zec-tip-bot/.env')
KEYS = ('X_USER_CLIENT_ID', 'X_USER_CLIENT_SECRET')


def updated_environment(raw, client_id, client_secret):
    for value in (client_id, client_secret):
        if not re.fullmatch(r'[!-~]{1,4096}', value) or any(c in value for c in ('"', "'", '\\', '$', '`')):
            raise ValueError('Invalid credential format; use the OAuth 2.0 Web App credentials.')
    if ':' in client_id:
        raise ValueError('Invalid Client ID format.')
    text = raw.decode('utf-8')
    for line in text.splitlines():
        if re.match(r'^\s*(?:export\s+)?X_USER_CLIENT_(?:ID|SECRET)\s*=', line) and not any(line.startswith(key + '=') for key in KEYS):
            raise ValueError('Use canonical signup credential assignments; no changes made.')
    for key, value in zip(KEYS, (client_id, client_secret)):
        pattern = r'(?m)^' + key + r'=.*$'
        if len(re.findall(pattern, text)) > 1:
            raise ValueError('Duplicate signup credential settings; no changes made.')
        line = key + '="' + value + '"'
        text = re.sub(pattern, lambda _: line, text) if re.search(pattern, text) else text.rstrip('\r\n') + '\n' + line + '\n'
    return text.encode('utf-8')


def save(path, previous, replacement):
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('Expected a root-owned private .env file with permissions 600.')
    if path.read_bytes() != previous:
        raise ValueError('The environment changed; start again. No changes made.')
    fd, temporary = tempfile.mkstemp(prefix='.user-signin-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if sys.platform != 'linux' or os.geteuid() != 0 or not sys.stdin.isatty():
        raise ValueError('Run interactively in the existing VPS root SSH terminal, not Windows or systemd-run.')
    os.umask(0o077)
    print('Enter the OAuth 2.0 Web App Client ID and Client Secret from X Keys and Tokens.')
    print('Both entries are hidden. Do not use the API key, API secret or bot access token.')
    client_id = getpass.getpass('Client ID (hidden): ').strip()
    client_secret = getpass.getpass('Client Secret (hidden): ').strip()
    # Read only on the operator's server. Never log the environment or its contents.
    info = ENV.lstat()
    if ENV.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('Expected a root-owned private .env file with permissions 600.')
    previous = ENV.read_bytes()
    replacement = updated_environment(previous, client_id, client_secret)
    save(ENV, previous, replacement)
    print(json.dumps({'userSigninCredentialsSaved': True, 'credentialsPrinted': False,
        'botCredentialsOrWalletSettingsChanged': False, 'servicesRestarted': False,
        'xRequestsMade': False, 'nextStep': 'Run update-user-signin.py to activate the tested sign-in change.'}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Signup configuration stopped: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__))
        raise SystemExit(1)
