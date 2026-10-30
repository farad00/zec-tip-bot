import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


config = load('configure_signup', 'configure-user-signin.py')
update = load('update_signup', 'update-user-signin.py')


class UserSigninUpdateTests(unittest.TestCase):
    def test_only_new_client_assignments_change(self):
        raw = b'# private synthetic fixture\nX_API_KEY="BOT-KEY"\nX_ACCESS_TOKEN="BOT-TOKEN"\nDOMAIN=zectip.app\nDAILY_X_BUDGET_USD=1.00\n'
        result = config.updated_environment(raw, 'client-id', 'client-secret')
        self.assertTrue(result.startswith(raw))
        self.assertEqual(result.count(b'X_USER_CLIENT_ID='), 1)
        self.assertEqual(result.count(b'X_USER_CLIENT_SECRET='), 1)
        changed = config.updated_environment(result, 'other-id', 'other-secret')
        self.assertTrue(changed.startswith(raw))
        self.assertNotIn(b'client-secret', changed)
        self.assertIn(b'X_USER_CLIENT_ID="other-id"', changed)

    def test_dangerous_or_ambiguous_inputs_are_not_written(self):
        for value in ('', 'has space', 'line\nbreak', 'quote"value', 'shell$value', '`command`', '\\escape'):
            with self.assertRaises(ValueError):
                config.updated_environment(b'X_API_KEY=fixture\n', 'id', value)
        for raw in (b'X_USER_CLIENT_ID=a\nX_USER_CLIENT_ID=b\n', b'export X_USER_CLIENT_ID=a\n', b' X_USER_CLIENT_SECRET = a\n'):
            with self.assertRaises(ValueError):
                config.updated_environment(raw, 'id', 'secret')

    def test_configuration_cannot_run_from_windows_or_noninteractive_unit(self):
        with patch.object(config.sys, 'platform', 'win32'), self.assertRaises(ValueError):
            config.main()

    def test_archive_is_exact_public_source_and_does_not_include_bot_auth_or_private_environment(self):
        archive = ROOT / 'zec-user-signin.zip'
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), update.ARCHIVE_SHA)
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(set(z.namelist()), update.FILES)
            self.assertEqual(len(z.namelist()), len(update.FILES))
            self.assertNotIn('.env', z.namelist())
            self.assertNotIn('src/auth.ts', z.namelist())
            self.assertNotIn('deploy/wallet-transfer-broker.py', z.namelist())
            for name in z.namelist():
                self.assertLessEqual(z.getinfo(name).file_size, 100000)
                self.assertEqual(z.read(name), (ROOT / 'zec-tip-bot' / name).read_bytes())

    def test_installer_probe_is_local_and_never_follows_the_authorize_url(self):
        self.assertIn('hostname:"127.0.0.1"', update.PROBE)
        self.assertIn('users.read tweet.read', update.PROBE)
        self.assertNotIn('await fetch(', update.PROBE)
        self.assertNotIn('console.log', update.PROBE)

    def test_parent_installer_keeps_rollback_and_idle_guards(self):
        parent = ROOT / 'update-zectip-logo.py'
        self.assertEqual(hashlib.sha256(parent.read_bytes()).hexdigest(), update.HELPER_SHA)
        source = parent.read_text(encoding='utf-8')
        self.assertIn('idle.native_idle()', source)
        self.assertIn('visible.install(base,health)', source)
        base = (ROOT / 'update-dashboard.py').read_text(encoding='utf-8')
        self.assertIn('Restoring the previous website image and source.', base)
        self.assertIn('protected_files()!=baseline', base)


if __name__ == '__main__':
    unittest.main()
