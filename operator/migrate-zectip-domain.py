#!/usr/bin/env python3
"""Move the existing Testnet website to zectip.app; retain the submitted address."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time
import urllib.error
import urllib.request

APP=Path('/srv/zec-tip-bot')
ENV=APP/'.env'
COMPOSE=APP/'deploy/compose.yaml'
CADDY=APP/'deploy/Caddyfile'
BACKUP=APP/'.update-backups/domain-zectip-app'
DOMAIN='zectip.app'
OLD_HOST='178-18-247-12.sslip.io'
ADDRESS='178.18.247.12'
ORIGIN='https://'+DOMAIN
OLD_ORIGIN='https://'+OLD_HOST
MARKER='ZEC Tip Bot domain setup in progress.'
SELECTED={'DOMAIN':DOMAIN,'PUBLIC_BASE_URL':ORIGIN,'X_USER_CALLBACK_URL':ORIGIN+'/account/callback'}
RUNTIME='''import {loadConfig} from "./dist/src/config.js";const c=loadConfig();if(!c.privateTips||c.network!=="testnet"||!c.hostedWallets||!c.walletAccounts)process.exit(1);console.log(JSON.stringify({baseUrl:c.baseUrl,userCallback:c.userCallback,botId:c.botId,posting:c.posting,hostedWallets:c.hostedWallets,usageGuard:c.usageGuard,pollSeconds:c.pollSeconds,mode:process.env.BOT_MODE}));'''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def public_read(url,method='GET'):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    request=urllib.request.Request(url,method=method,data=b'' if method=='POST' else None)
    try:response=opener.open(request,timeout=10)
    except urllib.error.HTTPError as error:response=error
    with response:return response.code,response.headers.get('Location'),response.read(65536).decode('utf-8',errors='replace')


def protected(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid!=0 or info.st_mode&0o022:
        raise ValueError('Expected an existing protected deployment file.')
    return info


def dns_ready(host):
    try:
        addresses={r[4][0] for r in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)}
        return bool(addresses) and addresses=={ADDRESS}
    except OSError:return False


def env_values(raw):
    text=raw.decode('utf-8');result={}
    for key in SELECTED:
        matched=re.findall(r'(?m)^'+key+r'=(.*)$',text)
        if len(matched)>1:raise ValueError('Duplicate domain-related environment settings require review.')
        if matched:result[key]=matched[0].strip().strip('"\'')
    return result


def new_environment(raw):
    # Preserve every credential value and unrelated setting. No values
    # outside the three selected public-domain settings are parsed or printed.
    env_values(raw)
    lines=raw.splitlines(keepends=True);ending=b'\r\n' if b'\r\n' in raw else b'\n';found=set();result=[]
    for line in lines:
        key=line.split(b'=',1)[0].decode('ascii',errors='ignore')
        if key in SELECTED:
            result.append((key+'='+SELECTED[key]).encode()+ending);found.add(key)
        else:result.append(line)
    if result and not result[-1].endswith(b'\n'):result[-1]+=ending
    for key in SELECTED:
        if key not in found:result.append((key+'='+SELECTED[key]).encode()+ending)
    return b''.join(result)


def new_compose(raw):
    value=json.loads(raw)
    if set(value.get('services',{}))!={'bot','proxy'}:raise ValueError('Expected the existing two-service deployment.')
    for service in value['services'].values():
        env=service.get('environment',{})
        if not isinstance(env,dict):raise ValueError('Unexpected explicit Compose environment format.')
        for key,new in SELECTED.items():
            if key in env and not str(env[key]).startswith('${'):env[key]=new
    return (json.dumps(value,indent=2)+'\n').encode()


def warm_caddy():
    return f'''{OLD_HOST} {{
    encode zstd gzip
    reverse_proxy bot:3001
}}
{DOMAIN}, www.{DOMAIN} {{
    header Cache-Control "no-store"
    header Referrer-Policy "no-referrer"
    respond "{MARKER}" 200
}}
'''.encode()


def alias_caddy(host):
    return f'''{host} {{
    header Referrer-Policy "no-referrer"
    header Cache-Control "no-store"
    @callback {{
        method GET HEAD
        path /account/callback
    }}
    @read method GET HEAD
    route {{
        handle @callback {{
            redir {ORIGIN}/account 303
        }}
        handle @read {{
            redir {ORIGIN}{{uri}} 308
        }}
        handle {{
            respond "This site moved. Open {ORIGIN}/account and sign in again." 409
        }}
    }}
}}
'''


def final_caddy():
    return (f'''{DOMAIN} {{
    encode zstd gzip
    reverse_proxy bot:3001
}}
'''+alias_caddy(OLD_HOST)+alias_caddy('www.'+DOMAIN)).encode()


def write_caddy(raw):
    # The file is bind-mounted individually into Caddy. Keep its inode so a
    # validated reload observes the new bytes without remounting other volumes.
    protected(CADDY)
    with CADDY.open('wb') as file:file.write(raw);file.flush();os.fsync(file.fileno())


def reload_caddy(base):
    base.compose('exec','-T','proxy','caddy','validate','--config','/etc/caddy/Caddyfile','--adapter','caddyfile')
    base.compose('exec','-T','proxy','caddy','reload','--config','/etc/caddy/Caddyfile','--adapter','caddyfile')


def website_idle(base,busy):
    running=base.compose('ps','--status','running','-q','bot').stdout.strip()
    if running:base.compose('exec','-T','bot','node','--input-type=module','-e',busy.IDLE)
    else:base.compose('run','--rm','--no-deps','bot','node','--input-type=module','-e',busy.IDLE)
    busy.native_idle()


def tls_warm():
    for host in (DOMAIN,'www.'+DOMAIN):
        status,_,body=public_read('https://'+host+'/')
        if status!=200 or body!=MARKER:return False
    return True


def verify_redirects():
    checks={
        'newWebsiteReachable':public_read(ORIGIN+'/')[0]==200,
        'newSignInReachable':'Continue with X' in public_read(ORIGIN+'/account')[2],
        'submittedLinkRedirects':public_read(OLD_ORIGIN+'/')[:2]==(308,ORIGIN+'/'),
        'oldAccountLinkRedirects':public_read(OLD_ORIGIN+'/account?panel=settings')[:2]==(308,ORIGIN+'/account?panel=settings'),
        'oldCallbackQueryNotForwarded':public_read(OLD_ORIGIN+'/account/callback?oauth_token=synthetic-check&oauth_verifier=synthetic-check')[:2]==(303,ORIGIN+'/account'),
        'oldPostRequestsNotReplayed':public_read(OLD_ORIGIN+'/__domain_migration_probe__','POST')[0]==409,
        'wwwRedirects':public_read('https://www.'+DOMAIN+'/')[:2]==(308,ORIGIN+'/')}
    if not all(checks.values()):raise ValueError('Domain or legacy-link verification did not pass.')
    return checks


def helpers():
    path=Path('/root/update-visible-settings.py');protected(path)
    spec=importlib.util.spec_from_file_location('domain_visible',path);visible=importlib.util.module_from_spec(spec);spec.loader.exec_module(visible)
    base=visible.load(Path('/root/update-dashboard.py'),'domain_base',visible.BASE_SHA)
    atomic=visible.load(Path('/root/install-public-enrollment.py'),'domain_atomic')
    busy=visible.load(Path('/root/update-confirming-balance.py'),'domain_idle')
    return base,atomic,busy


def report(checks):
    return {'domainMigrationComplete':True,'canonicalAddress':ORIGIN,'submittedLinkKeptWorking':True,
        **checks,'walletDataMoved':False,'walletOrNodeRestarted':False,'credentialsPrinted':False,
        'workerConfigurationPreserved':True,'newPaymentsRequestedByHelper':False,
        'newXSignInStillToVerify':True}


def expected_runtime(runtime):
    return {**runtime,'baseUrl':ORIGIN,'userCallback':ORIGIN+'/account/callback'}


def read_runtime(base):
    return json.loads(base.compose('exec','-T','bot','node','--input-type=module','-e',RUNTIME).stdout)


def activate(base,atomic,busy):
    before_env=ENV.read_bytes();before_caddy=CADDY.read_bytes();before_compose=COMPOSE.read_bytes()
    normalized=before_caddy.decode().replace('\r','').strip()
    if normalized!='{$DOMAIN} {\n    encode zstd gzip\n    reverse_proxy bot:3001\n}':raise ValueError('The current proxy has custom routes; its configuration was preserved.')
    after_env=new_environment(before_env);after_compose=new_compose(before_compose)
    runtime=read_runtime(base)
    if runtime['baseUrl']!=OLD_ORIGIN:raise ValueError('Running website and environment do not agree.')
    base.compose('exec','-T','bot','node','--input-type=module','-e',busy.IDLE);busy.native_idle()
    BACKUP.mkdir(mode=0o700,parents=True)
    for name,raw in (('previous.env',before_env),('previous.compose.json',before_compose),('previous.Caddyfile',before_caddy)):(BACKUP/name).write_bytes(raw)
    (BACKUP/'runtime-before.json').write_text(json.dumps(runtime),encoding='utf-8')
    phase=BACKUP/'state.json'
    def state(value):phase.write_text(json.dumps({'phase':value,'target':ORIGIN}),encoding='utf-8')
    stopped=False;switched=False
    try:
        state('warming_https');print('Preparing HTTPS on the new domain while the submitted address stays available...',flush=True)
        write_caddy(warm_caddy());reload_caddy(base)
        for attempt in range(24):
            try:
                if tls_warm():break
            except Exception:pass
            if attempt==23:raise ValueError('HTTPS is not ready on the new domain; old configuration will be restored.')
            time.sleep(5)
        # Check the final routing syntax before restarting the website. Writing
        # the bind-mounted file does not replace Caddy's active configuration.
        write_caddy(final_caddy())
        base.compose('exec','-T','proxy','caddy','validate','--config','/etc/caddy/Caddyfile','--adapter','caddyfile')
        write_caddy(warm_caddy())
        if ENV.read_bytes()!=before_env or COMPOSE.read_bytes()!=before_compose:raise ValueError('Deployment settings changed during HTTPS preparation.')
        base.compose('exec','-T','bot','node','--input-type=module','-e',busy.IDLE);busy.native_idle()
        state('switching_origin');base.compose('stop','-t','45','bot',quiet=False);stopped=True
        base.compose('run','--rm','--no-deps','bot','node','--input-type=module','-e',busy.IDLE);busy.native_idle()
        switched=True
        atomic.replace(ENV,after_env,protected(ENV));atomic.replace(COMPOSE,after_compose,protected(COMPOSE))
        base.compose('up','-d','--no-deps','--force-recreate','bot',quiet=False);stopped=False;base.wait_web()
        current=read_runtime(base)
        expected=expected_runtime(runtime)
        if current!=expected:raise ValueError('Domain change altered another website runtime setting.')
        write_caddy(final_caddy());reload_caddy(base)
        checks=verify_redirects();state('complete')
        return report(checks)
    except BaseException:
        if switched:
            # Origin changes do not alter financial terms or stored jobs. Wait
            # for current native work before any rollback restart.
            try:
                website_idle(base,busy)
            except Exception:
                state('needs_review_keep_new_runtime')
                raise ValueError('New runtime was preserved because payment/setup work is active. Do not repeat the migration; inspect its saved state.') from None
            base.compose('stop','-t','45','bot',quiet=False);stopped=True
            atomic.replace(ENV,before_env,protected(ENV));atomic.replace(COMPOSE,before_compose,protected(COMPOSE))
        write_caddy(before_caddy);reload_caddy(base)
        if switched:base.compose('up','-d','--no-deps','--force-recreate','bot',quiet=False);stopped=False
        state('rolled_back')
        raise
    finally:
        if stopped:base.compose('start','bot',quiet=False)


def resume_routes(base):
    # A routing-only retry must never restart an active payment worker. The
    # original public runtime snapshot also guards against an unrelated change.
    saved=BACKUP/'runtime-before.json';protected(saved)
    original=json.loads(saved.read_bytes())
    if read_runtime(base)!=expected_runtime(original):raise ValueError('The running website differs from the saved domain migration settings.')
    if env_values(ENV.read_bytes())!=SELECTED:raise ValueError('The domain-related environment differs from the target.')
    write_caddy(final_caddy());reload_caddy(base)
    checks=verify_redirects()
    (BACKUP/'state.json').write_text(json.dumps({'phase':'complete','target':ORIGIN}),encoding='utf-8')
    return report(checks)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--callback-registered',action='store_true',help='Operator confirms the new callback was added to the existing X app.')
    args=parser.parse_args()
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the VPS root terminal.')
    os.umask(0o077)
    domain_dns=dns_ready(DOMAIN);www_dns=dns_ready('www.'+DOMAIN)
    protected(ENV);values=env_values(ENV.read_bytes())
    if not args.apply:
        print(json.dumps({'readOnly':True,'target':ORIGIN,'domainDnsPointsToServer':domain_dns,'wwwDnsPointsToServer':www_dns,
            'alreadyConfigured':values.get('PUBLIC_BASE_URL')==ORIGIN,'requiredXCallback':SELECTED['X_USER_CALLBACK_URL'],
            'existingSubmittedAddress':OLD_ORIGIN,'credentialsPrinted':False},indent=2));return
    if not args.callback_registered:raise ValueError('Add https://zectip.app/account/callback to the existing X app first, then use --callback-registered.')
    if not domain_dns or not www_dns:raise ValueError('Point the domain A record and www CNAME at the server first. Use DNS-only records, with no conflicting parking/AAAA records.')
    for path in (ENV,COMPOSE,CADDY):protected(path)
    if values.get('PUBLIC_BASE_URL')==ORIGIN:
        base,_,_=helpers()
        print(json.dumps(resume_routes(base),indent=2));return
    if values.get('PUBLIC_BASE_URL')!=OLD_ORIGIN or values.get('DOMAIN')!=OLD_HOST:raise ValueError('The current configured address differs from the submitted deployment.')
    if BACKUP.exists() or BACKUP.is_symlink():raise ValueError('A prior domain migration backup exists. It was preserved for review.')
    base,atomic,busy=helpers()
    print(json.dumps(activate(base,atomic,busy),indent=2))
    print('Open https://zectip.app/account and sign in with X again. Existing wallet mappings stay linked.')


if __name__=='__main__':
    try:main()
    except Exception as error:print('Domain migration stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__));raise SystemExit(1)
