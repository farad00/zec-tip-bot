import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import shutil
import sys
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location('reply_guidance_release', ROOT / 'update-reply-guidance.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class ReplyGuidanceUpdateTests(unittest.TestCase):
    def test_release_is_exact_and_contains_no_credentials_or_native_changes(self):
        path = ROOT / 'zec-reply-guidance.zip'
        if not path.exists():
            path = ROOT / 'releases' / path.name
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), update.ARCHIVE_SHA)
        with zipfile.ZipFile(path) as z:
            self.assertEqual(set(z.namelist()), update.FILES)
            self.assertEqual(len(z.namelist()), len(update.FILES))
            self.assertIsNone(z.testzip())
            self.assertTrue(all(not name.startswith(('deploy/wallet-', '.env', 'src/user-auth')) for name in z.namelist()))
            self.assertIn(b'Configure and enable tipping first:', z.read('src/private-tips.ts'))
            self.assertNotIn(b'Check status or create a wallet to receive tips:', z.read('src/private-tips.ts'))

    def test_installer_retains_verified_idle_and_rollback_wrapper(self):
        parent = ROOT / 'update-zectip-logo.py'
        self.assertEqual(hashlib.sha256(parent.read_bytes()).hexdigest(), update.HELPER_SHA)
        source = parent.read_text(encoding='utf-8')
        self.assertIn('idle.native_idle()', source)
        self.assertIn('visible.install(base,health)', source)

    def test_probe_uses_in_memory_state_and_no_network_or_wallet(self):
        self.assertIn('new State(":memory:")', update.PROBE)
        self.assertNotIn('fetch(', update.PROBE)
        self.assertNotIn('.apply(', update.PROBE)
        self.assertNotIn('WalletAccountClient', update.PROBE)
        self.assertNotIn('/app/data', update.PROBE)

    def test_probe_executes_against_compiled_app_without_a_private_environment_file(self):
        node = r'C:\Program Files\nodejs\node.exe' if sys.platform == 'win32' else shutil.which('node')
        app = ROOT / 'zec-tip-bot'
        if not app.exists():
            app = ROOT.parent
        result = subprocess.run([node, '--input-type=module', '-e', update.PROBE], cwd=app,
            env={'SystemRoot':os.environ.get('SystemRoot',r'C:\Windows'), 'PRIVATE_TIPPING_ENABLED':'true', 'ZCASH_NETWORK':'testnet', 'X_BOT_USER_ID':'100'},
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_operator_script_refuses_non_linux_execution(self):
        with patch.object(update.sys, 'platform', 'win32'), self.assertRaises(ValueError):
            update.main()


if __name__ == '__main__':
    unittest.main()
