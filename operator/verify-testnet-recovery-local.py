#!/usr/bin/env python3
"""Human-run local recovery verification. Never print or upload recovery material."""
import argparse
from contextlib import closing
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tarfile
import tomllib
import unicodedata
import uuid

ARCHIVE_NAME = 'testnet-recovery-20261028T150138Z-e6bb0702.tar.age'
EXPECTED_SHA = 'b5a3036668ce4e8641a26be7090d9ef34bf1405adae23de7f193d7409c61bccd'
REVISION = '8c5a93ba9d68ad78d199c3624413c0139781b837'
LIMITS = {'wallet.db': 512 * 1024 * 1024, 'mnemonic.age': 16384,
          'encryption-identity.txt': 16384, 'zallet.toml': 65536, 'manifest.json': 16384}


class RecoveryError(Exception):
    """Only fixed, non-sensitive messages of this type may be displayed."""


def checked_decrypt(age, arguments, input_data=None):
    result = subprocess.run([str(age), '--decrypt', *arguments], input=input_data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    if result.returncode:
        raise RecoveryError('Decryption failed. Check the local recovery key and encrypted backup.')
    return result.stdout


def read_archive(raw):
    if len(raw) > sum(LIMITS.values()) + 65536:
        raise RecoveryError('The decrypted archive exceeds the expected bootstrap size.')
    files = {}
    # Deliberately uncompressed tar; never extract files onto disk or accept links.
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for entry in archive:
            if (entry.name not in LIMITS or entry.name in files or not entry.isfile()
                    or entry.size < 1 or entry.size > LIMITS[entry.name]):
                raise RecoveryError('The archive contains an unexpected file, link or size.')
            source = archive.extractfile(entry)
            if source is None:
                raise RecoveryError('An expected recovery file could not be read.')
            with source:
                content = source.read(LIMITS[entry.name] + 1)
            if len(content) != entry.size:
                raise RecoveryError('A recovery file is incomplete.')
            files[entry.name] = content
    if set(files) != set(LIMITS):
        raise RecoveryError('The recovery archive is missing expected files.')
    manifest = json.loads(files['manifest.json'])
    if (manifest.get('schema') != 1 or manifest.get('network') != 'testnet'
            or manifest.get('sourceRevision') != REVISION):
        raise RecoveryError('The archive does not match the expected Testnet wallet build.')
    hashes = manifest.get('fileSha256', {})
    if set(hashes) != set(LIMITS) - {'manifest.json'}:
        raise RecoveryError('The recovery manifest has an unexpected file list.')
    for name, expected in hashes.items():
        if not isinstance(expected, str) or not hmac.compare_digest(hashlib.sha256(files[name]).hexdigest(), expected):
            raise RecoveryError('A recovery file does not match its manifest checksum.')
    config = tomllib.loads(files['zallet.toml'].decode())
    if (config.get('consensus', {}).get('network') != 'test'
            or config.get('keystore', {}).get('require_backup') is not True
            or config.get('external', {}).get('broadcast') is not False):
        raise RecoveryError('The backed-up wallet does not have the expected Testnet guards.')
    return files, manifest


def read_database(raw, manifest):
    if not raw.startswith(b'SQLite format 3\x00') or len(raw) < 100:
        raise RecoveryError('The wallet database has an invalid header.')
    # The native online-backup snapshot is transaction-consistent. SQLite documents
    # changing only the in-memory copy's journal-format bytes for deserialize:
    # https://sqlite.org/c3ref/deserialize.html
    memory_copy = bytearray(raw)
    if memory_copy[18] not in (1, 2) or memory_copy[19] not in (1, 2):
        raise RecoveryError('Unexpected SQLite journal format.')
    memory_copy[18] = memory_copy[19] = 1
    with closing(sqlite3.connect(':memory:')) as db:
        db.deserialize(bytes(memory_copy))
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RecoveryError('Wallet database integrity verification failed.')
        accounts = db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
        seeds = db.execute('SELECT hd_seed_fingerprint, backup_confirmed, encrypted_mnemonic '
                           'FROM ext_zallet_keystore_mnemonics').fetchall()
    if len(seeds) != 1 or len(seeds[0][0]) != 32 or seeds[0][1] not in (0, 1):
        raise RecoveryError('Expected exactly one recoverable operator seed.')
    fingerprint, confirmed, encrypted_seed = seeds[0]
    expected = manifest.get('seedFingerprintHex')
    if (not isinstance(expected, str) or not re.fullmatch(r'[A-Fa-f0-9]{64}', expected)
            or not hmac.compare_digest(fingerprint.hex().upper(), expected.upper())
            or type(manifest.get('walletAccounts')) is not int or accounts != manifest['walletAccounts']):
        raise RecoveryError('Wallet database metadata does not match the recovery manifest.')
    if not isinstance(encrypted_seed, bytes) or not 1 <= len(encrypted_seed) <= 16384:
        raise RecoveryError('Encrypted operator seed is missing or unexpectedly sized.')
    return {'fingerprint': fingerprint, 'confirmed': bool(confirmed),
            'accounts': accounts, 'encryptedSeed': encrypted_seed}


def mnemonic_seed(phrase, passphrase=''):
    # BIP39, native Zallet generation uses an empty optional passphrase.
    # https://github.com/bitcoin/bips/blob/master/bip-0039.mediawiki
    phrase = unicodedata.normalize('NFKD', phrase).encode()
    salt = unicodedata.normalize('NFKD', 'mnemonic' + passphrase).encode()
    return hashlib.pbkdf2_hmac('sha512', phrase, salt, 2048, dklen=64)


def seed_fingerprint(seed):
    # ZIP32 includes a LENGTH BYTE, not only seed bytes. Do not omit it.
    # https://zips.z.cash/zip-0032#seed-fingerprints
    if not 32 <= len(seed) <= 252:
        raise RecoveryError('Unexpected wallet seed length.')
    return hashlib.blake2b(bytes([len(seed)]) + seed, digest_size=32,
                           person=b'Zcash_HD_Seed_FP').digest()


def decrypt_inner(age, identity, ciphertext, folder):
    # Only encrypted bytes touch a temporary file. The restored private identity
    # enters age via stdin; neither it nor plaintext words are written to disk.
    path = folder / ('verify-encrypted-' + uuid.uuid4().hex + '.age')
    if path.resolve().parent != folder.resolve():
        raise RecoveryError('Unexpected encrypted temporary-file location.')
    try:
        with path.open('xb') as file:
            file.write(ciphertext)
        result = checked_decrypt(age, ['--identity', '-', str(path)], input_data=identity)
        if len(result) > 4096:
            raise RecoveryError('Decrypted mnemonic is unexpectedly large.')
        return result
    finally:
        if path.exists():
            if path.is_symlink() or path.resolve().parent != folder.resolve():
                raise RecoveryError('Temporary file location changed; manual inspection required.')
            path.unlink()


def verify_words(exported, stored, expected_fingerprint):
    if not hmac.compare_digest(exported, stored):
        raise RecoveryError('Exported recovery words do not match the backed-up database.')
    phrase = exported.decode('utf-8').strip()
    words = phrase.split()
    if len(words) != 24 or any(not re.fullmatch(r'[a-z]{1,16}', word) for word in words):
        raise RecoveryError('The operator phrase is not the expected 24-word format.')
    if not hmac.compare_digest(seed_fingerprint(mnemonic_seed(phrase)), expected_fingerprint):
        raise RecoveryError('The recovered phrase does not reproduce the wallet seed fingerprint.')
    return words


def show_words(words):
    import tkinter as tk
    root = tk.Tk()
    root.title('Private recovery — ZEC Tip Bot Testnet')
    root.configure(bg='#f7f6ef')
    root.geometry('860x600')
    root.minsize(800, 570)
    frame = tk.Frame(root, bg='#f7f6ef', padx=28, pady=24)
    frame.pack(fill='both', expand=True)
    tk.Label(frame, text='Write down your 24 recovery words', font=('Segoe UI', 20, 'bold'),
             bg='#f7f6ef', fg='#17241f').pack(anchor='w')
    tk.Label(frame, text='TESTNET OPERATOR WALLET · Keep this window private. Do not screenshot or paste it into chat.',
             font=('Segoe UI', 10), bg='#f7f6ef', fg='#6b3933', wraplength=780,
             justify='left').pack(anchor='w', pady=(8, 20))
    grid = tk.Frame(frame, bg='#f7f6ef')
    grid.pack(fill='x')
    for index, word in enumerate(words):
        label = tk.Label(grid, text=f'{index+1:02d}   {word}', font=('Consolas', 15),
                         bg='white', fg='#17241f', anchor='w', padx=12, pady=10)
        label.grid(row=index % 6, column=index // 6, sticky='ew', padx=4, pady=4)
    for column in range(4):
        grid.columnconfigure(column, weight=1)
    recorded = tk.BooleanVar(value=False)
    tk.Checkbutton(frame, text='I have recorded all 24 words securely and in order.', variable=recorded,
                   bg='#f7f6ef', activebackground='#f7f6ef', font=('Segoe UI', 11)).pack(anchor='w', pady=(18, 8))
    answer = {'recorded': False}
    def finish():
        if recorded.get():
            answer['recorded'] = True
            root.destroy()
    tk.Button(frame, text='Close private recovery window', command=finish,
              font=('Segoe UI', 11, 'bold'), bg='#ebc348', padx=14, pady=8).pack(anchor='w')
    root.mainloop()
    return answer['recorded']


def verify(age, archive, local_identity, expected_sha, folder):
    if archive.is_symlink() or local_identity.is_symlink() or not archive.is_file() or not local_identity.is_file():
        raise RecoveryError('The downloaded archive or local recovery key is missing or is a link.')
    if not 100 <= archive.stat().st_size <= sum(LIMITS.values()) + 1048576:
        raise RecoveryError('Unexpected encrypted archive size.')
    with archive.open('rb') as file:
        digest = hashlib.file_digest(file, 'sha256').hexdigest()
    if not re.fullmatch(r'[a-f0-9]{64}', expected_sha) or not hmac.compare_digest(digest, expected_sha):
        raise RecoveryError('Encrypted archive checksum does not match the server report.')
    raw = checked_decrypt(age, ['--identity', str(local_identity), str(archive)])
    files, manifest = read_archive(raw)
    database = read_database(files['wallet.db'], manifest)
    exported = decrypt_inner(age, files['encryption-identity.txt'], files['mnemonic.age'], folder)
    stored = decrypt_inner(age, files['encryption-identity.txt'], database['encryptedSeed'], folder)
    words = verify_words(exported, stored, database['fingerprint'])
    report = {'network': 'testnet', 'encryptedBackupSha256': digest,
              'encryptedArchiveChecksumMatches': True, 'archiveFilesVerified': len(files),
              'walletDatabaseIntegrityVerified': True, 'operatorSeedRecoveryVerified': True,
              'recoveredSeedMatchesWallet': True, 'recoveryWordCount': len(words),
              'walletAccountsInSnapshot': database['accounts'],
              'nativeBackupConfirmedInSnapshot': database['confirmed'],
              'nativeWalletRestoreVerified': False, 'changesServer': False}
    return report, words


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show-words', action='store_true', help='Display recovery words privately in a local window.')
    args = parser.parse_args()
    if os.name != 'nt':
        raise RecoveryError('Run this locally in Windows PowerShell, never on the VPS.')
    folder = Path(os.environ['LOCALAPPDATA']) / 'ZecTipBotRecovery'
    age = Path(os.environ['LOCALAPPDATA']) / 'Microsoft/WinGet/Packages/FiloSottile.age_Microsoft.Winget.Source_8wekyb3d8bbwe/age/age.exe'
    if not age.is_file() or not folder.is_dir() or folder.is_symlink():
        raise RecoveryError('Expected the installed age executable and existing private local recovery folder.')
    report, words = verify(age, folder / ARCHIVE_NAME, folder / 'backup-key.txt', EXPECTED_SHA, folder)
    report['operatorRecordedWordsThisRun'] = show_words(words) if args.show_words else False
    words.clear()
    receipt = folder / 'recovery-verification.json'
    if receipt.is_symlink():
        raise RecoveryError('Local verification receipt must not be a symbolic link.')
    temporary = folder / ('verification-' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as file:
        json.dump(report, file, indent=2)
    os.replace(temporary, receipt)
    print(json.dumps(report, indent=2))
    print('Share only this status report. Keep the private recovery window and key files private.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'localRecoveryCheck': 'failed', 'reason': str(error)
                          if isinstance(error, RecoveryError) else type(error).__name__}), file=sys.stderr)
        sys.exit(1)
