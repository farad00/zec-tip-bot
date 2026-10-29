import copy
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location('domain_update',ROOT/'migrate-zectip-domain.py')
update=importlib.util.module_from_spec(spec);spec.loader.exec_module(update)
ORIGINAL_CADDY=b'{$DOMAIN} {\n    encode zstd gzip\n    reverse_proxy bot:3001\n}\n'


class DomainMigrationTests(unittest.TestCase):
    def test_only_public_origin_fields_are_changed(self):
        before=(b'# credentials stay local\r\nX_API_SECRET=synthetic-value\r\n'
                b'DOMAIN=178-18-247-12.sslip.io\r\nPUBLIC_BASE_URL=https://178-18-247-12.sslip.io\r\n'
                b'X_USER_CALLBACK_URL=https://178-18-247-12.sslip.io/account/callback\r\n'
                b'X_CALLBACK_URL=http://127.0.0.1:3000/callback\r\nX_USAGE_GUARD_ENABLED=false\r\nBOT_MODE=start')
        after=update.new_environment(before)
        self.assertEqual(update.env_values(after),update.SELECTED)
        for line in before.splitlines():
            if line.split(b'=',1)[0].decode() not in update.SELECTED:self.assertIn(line,after.splitlines())
        self.assertEqual(update.new_environment(after),after)

    def test_duplicate_origin_settings_are_refused(self):
        with self.assertRaises(ValueError):update.new_environment(b'DOMAIN=one\nDOMAIN=two\n')

    def test_compose_preserves_wallet_storage_and_worker_controls(self):
        before={'services':{'bot':{'environment':{'PUBLIC_BASE_URL':update.OLD_ORIGIN,'BOT_MODE':'start','HOSTED_WALLETS_ENABLED':'true'},'volumes':['bot-data:/app/data','/run/broker:/run/broker']},
                            'proxy':{'environment':{'DOMAIN':'${DOMAIN:?required}'},'volumes':['caddy-data:/data']}},'volumes':{'bot-data':{},'caddy-data':{}}}
        expected=copy.deepcopy(before);expected['services']['bot']['environment']['PUBLIC_BASE_URL']=update.ORIGIN
        self.assertEqual(json.loads(update.new_compose(json.dumps(before).encode())),expected)
        for invalid in ({'services':{}},{'services':{'bot':{'environment':['DOMAIN=bad']},'proxy':{}}}):
            with self.assertRaises(ValueError):update.new_compose(json.dumps(invalid).encode())

    def test_dns_requires_the_server_and_rejects_conflicting_ipv6(self):
        a=(socket.AF_INET,socket.SOCK_STREAM,6,'',(update.ADDRESS,443))
        aaaa=(socket.AF_INET6,socket.SOCK_STREAM,6,'',('2001:db8::1',443,0,0))
        with patch.object(update.socket,'getaddrinfo',return_value=[a]):self.assertTrue(update.dns_ready(update.DOMAIN))
        for records in ([],[a,aaaa]):
            with patch.object(update.socket,'getaddrinfo',return_value=records):self.assertFalse(update.dns_ready(update.DOMAIN))

    def test_alias_routes_strip_callback_secrets_and_refuse_post_replay(self):
        text=update.final_caddy().decode()
        self.assertEqual(text.count('redir https://zectip.app/account 303'),2)
        self.assertEqual(text.count('redir https://zectip.app{uri} 308'),2)
        self.assertEqual(text.count('method GET HEAD'),4)
        self.assertEqual(text.count('respond "This site moved.'),2)
        self.assertNotIn('permanent',text)
        self.assertIn('reverse_proxy bot:3001',text)
        self.assertLess(text.index('handle @callback'),text.index('handle @read'))

    def test_public_probes_validate_each_redirect(self):
        def read(url,method='GET'):
            if url==update.ORIGIN+'/':return 200,None,'home'
            if url==update.ORIGIN+'/account':return 200,None,'Continue with X'
            if method=='POST':return 409,None,'moved'
            if '/account/callback?' in url:return 303,update.ORIGIN+'/account',''
            if '?panel=' in url:return 308,update.ORIGIN+'/account?panel=settings',''
            return 308,update.ORIGIN+'/',''
        with patch.object(update,'public_read',side_effect=read):self.assertTrue(all(update.verify_redirects().values()))
        with patch.object(update,'public_read',return_value=(200,None,'unexpected')):
            with self.assertRaises(ValueError):update.verify_redirects()

    def exercise(self,*,stage=None,active_during_rollback=False,resume=False):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            folder=Path(temp);env=folder/'env';compose=folder/'compose';caddy=folder/'Caddyfile';backup=folder/'backup'
            original_env=('DOMAIN='+update.OLD_HOST+'\nPUBLIC_BASE_URL='+update.OLD_ORIGIN+'\nBOT_MODE=start\nX_USAGE_GUARD_ENABLED=false\n').encode()
            original_compose=json.dumps({'services':{'bot':{'environment':{'BOT_MODE':'start'}},'proxy':{'environment':{'DOMAIN':'${DOMAIN:?required}'}}},'volumes':{'bot-data':{}}}).encode()
            env.write_bytes(original_env);compose.write_bytes(original_compose);caddy.write_bytes(ORIGINAL_CADDY)
            inode=caddy.stat().st_ino
            before_runtime={'baseUrl':update.OLD_ORIGIN,'userCallback':update.OLD_ORIGIN+'/account/callback','botId':'123','posting':True,'hostedWallets':True,'usageGuard':False,'pollSeconds':60,'mode':'start'}
            running=True;calls=[];writes=[];failed_once=False
            def command(*args,**kwargs):
                nonlocal running
                calls.append(args)
                if args[0]=='stop':running=False
                if args[0] in ('up','start'):running=True
                if args[0]=='ps':return SimpleNamespace(stdout='synthetic-running-container' if running else '')
                if args[-1]==update.RUNTIME:
                    current=update.env_values(env.read_bytes())
                    runtime=update.expected_runtime(before_runtime) if current.get('PUBLIC_BASE_URL')==update.ORIGIN else before_runtime
                    if stage=='changed_runtime' and current.get('PUBLIC_BASE_URL')==update.ORIGIN:runtime={**runtime,'usageGuard':True}
                    return SimpleNamespace(stdout=json.dumps(runtime))
                return SimpleNamespace(stdout='',returncode=0)
            def replace(path,raw,info=None):
                nonlocal failed_once
                writes.append(path)
                if stage=='partial_write' and path==compose and not failed_once:
                    failed_once=True;raise RuntimeError('synthetic disk failure')
                path.write_bytes(raw)
            health=Mock(side_effect=RuntimeError('synthetic health failure') if stage=='health' else None)
            base=SimpleNamespace(compose=Mock(side_effect=command),wait_web=health)
            atomic=SimpleNamespace(replace=replace)
            busy=SimpleNamespace(IDLE='synthetic idle query',native_idle=Mock())
            checks={'newWebsiteReachable':True,'submittedLinkRedirects':True}
            def rollback_gate(*args):
                if active_during_rollback:raise ValueError('synthetic active payment')
                return None
            with patch.multiple(update,ENV=env,COMPOSE=compose,CADDY=caddy,BACKUP=backup),patch.object(update,'protected',return_value=SimpleNamespace()),patch.object(update,'reload_caddy') as reloads,patch.object(update,'tls_warm',return_value=stage!='tls'),patch.object(update.time,'sleep'),patch.object(update,'verify_redirects',return_value=checks),patch.object(update,'website_idle',side_effect=rollback_gate):
                if stage:
                    with self.assertRaises((RuntimeError,ValueError)):update.activate(base,atomic,busy)
                    state=json.loads((backup/'state.json').read_bytes())['phase']
                    if active_during_rollback:
                        self.assertEqual(state,'needs_review_keep_new_runtime')
                        self.assertEqual(update.env_values(env.read_bytes()),update.SELECTED)
                        stop_count=sum(c[0]=='stop' for c in calls)
                        self.assertEqual(stop_count,1)
                        calls.clear()
                        # Routing-only recovery never calls stop/up/start or touches env.
                        health.side_effect=None
                        self.assertTrue(update.resume_routes(base)['domainMigrationComplete'])
                        self.assertFalse(any(c[0] in ('stop','up','start') for c in calls))
                    else:
                        self.assertEqual(state,'rolled_back')
                        self.assertEqual(env.read_bytes(),original_env)
                        self.assertEqual(compose.read_bytes(),original_compose)
                        self.assertEqual(caddy.read_bytes(),ORIGINAL_CADDY)
                        self.assertTrue(running)
                else:
                    self.assertTrue(update.activate(base,atomic,busy)['domainMigrationComplete'])
                    self.assertEqual(update.env_values(env.read_bytes()),update.SELECTED)
                    self.assertIn(b'BOT_MODE=start\nX_USAGE_GUARD_ENABLED=false\n',env.read_bytes())
                    self.assertEqual(caddy.read_bytes(),update.final_caddy())
                    if resume:
                        calls.clear();self.assertTrue(update.resume_routes(base)['domainMigrationComplete'])
                        self.assertFalse(any(c[0] in ('stop','up','start') for c in calls))
                self.assertEqual(inode,caddy.stat().st_ino)
                # Only environment, compose and Caddy changes; no wallet command or X API call.
                self.assertTrue(set(writes).issubset({env,compose}))
                self.assertFalse(any('zallet' in str(c) or 'systemctl' in str(c) or 'down' in c for c in calls))
                if stage=='tls':self.assertEqual(writes,[]);self.assertFalse(any(c[0]=='stop' for c in calls))

    def test_success_preserves_runtime_storage_and_caddy_bind_mount_inode(self):self.exercise()
    def test_repeat_changes_routes_only_without_restarting_workers(self):self.exercise(resume=True)
    def test_tls_failure_leaves_the_original_website_running(self):self.exercise(stage='tls')
    def test_partial_environment_write_restores_both_files(self):self.exercise(stage='partial_write')
    def test_failed_health_check_rolls_back_configuration(self):self.exercise(stage='health')
    def test_unexpected_runtime_control_change_is_rejected(self):self.exercise(stage='changed_runtime')
    def test_active_payment_prevents_rollback_restart_but_allows_route_recovery(self):self.exercise(stage='health',active_during_rollback=True)


if __name__=='__main__':unittest.main()
