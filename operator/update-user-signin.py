#!/usr/bin/env python3
"""Install read-scoped OAuth2 user sign-in without changing bot authorization."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

ARCHIVE_SHA = '254ce76f1fae45845cd3759083dfe59aa1ec3bd0c8eaa7574f53016ca2199092'
HELPER_SHA = '361110a0c980ffcd969d0138554304a01dc5e2cef7c393400a1fc841c83073bc'
FILES = {'src/account.ts', 'src/user-auth.ts', 'src/config.ts', 'src/deployment.ts',
    'test/private-tips.test.ts', 'test/user-auth.test.ts', 'test/deployment.test.ts',
    'Dockerfile', '.env.example', 'deploy/host.env.example', 'README.md', 'deploy/README.md'}
PROBE = '''import {request} from "node:http";
import {loadConfig} from "./dist/src/config.js";
import {USER_SIGNIN_SCOPES} from "./dist/src/user-auth.js";
const c=loadConfig();if(!c.privateTips||c.network!=="testnet"||!c.userClientId||!c.userClientSecret)process.exit(1);
if(USER_SIGNIN_SCOPES.join(" ")!=="users.read tweet.read")process.exit(1);
const read=(path,body,cookie)=>new Promise((resolve,reject)=>{
 const req=request({hostname:"127.0.0.1",port:3001,path,method:body?"POST":"GET",
  headers:{host:new URL(c.baseUrl).host,origin:c.baseUrl,...(body?{"content-type":"application/x-www-form-urlencoded"}:{}),...(cookie?{cookie}:{})}},res=>{
  let html="";res.setEncoding("utf8");res.on("data",chunk=>html+=chunk);res.on("end",()=>resolve({status:res.statusCode,headers:res.headers,html}));
 });req.setTimeout(4000,()=>req.destroy(new Error("Local sign-in check timed out")));req.on("error",reject);req.end(body);
});
const page=await read("/account");if(page.status!==200||page.headers["cache-control"]!=="no-store")process.exit(1);
const csrf=/name="csrf" value="([a-f0-9]+)"/.exec(page.html)?.[1];if(!csrf)process.exit(1);
const cookies=page.headers["set-cookie"].map(value=>value.split(";")[0]).join("; ");
const start=await read("/account/login","csrf="+csrf,cookies);
if(start.status!==303)process.exit(1);
const u=new URL(start.headers.location);
if(u.origin+u.pathname!=="https://x.com/i/oauth2/authorize"||u.searchParams.get("scope")!=="users.read tweet.read"||
 u.searchParams.get("code_challenge_method")!=="S256"||u.searchParams.get("redirect_uri")!==c.userCallback||
 !u.searchParams.get("state")||u.searchParams.has("oauth_token"))process.exit(1);
// Intentionally do not follow the redirect or contact X. No login or payment is requested.
'''


def main():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Run in the existing VPS root terminal.')
    os.umask(0o077)
    path = Path('/root/update-zectip-logo.py')
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022
            or hashlib.sha256(path.read_bytes()).hexdigest() != HELPER_SHA):
        raise ValueError('Expected the verified installed website updater.')
    # Source hashes only: ensure the independently authorized bot is not edited by this release.
    root = Path('/srv/zec-tip-bot')
    bot_sources = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in ('src/auth.ts', 'src/x.ts')}
    spec = importlib.util.spec_from_file_location('user_signin_base', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ARCHIVE = Path('/root/zec-user-signin.zip')
    module.ARCHIVE_SHA = ARCHIVE_SHA
    module.FILES = FILES
    module.PROBE = PROBE
    module.main()
    if any(hashlib.sha256((root / name).read_bytes()).hexdigest() != digest for name, digest in bot_sources.items()):
        raise ValueError('Bot authorization sources changed unexpectedly; inspect the deployment.')
    print(json.dumps({'userSigninUpdated': True, 'userAuthFlow': 'OAuth2 PKCE S256',
        'requestedUserScopes': ['users.read', 'tweet.read'], 'writeDmEmailAndOfflineScopesRequested': False,
        'userTokensPersisted': False, 'tokenRevocationAfterIdentityCheck': 'best effort',
        'legacyUserGrantsAutomaticallyRevoked': False, 'botAuthorizationSourcesUnchanged': True,
        'walletSettingsAndWelcomeBudgetChanged': False, 'nativeServicesChanged': False,
        'xCallsOrPaymentsRequestedByInstaller': False, 'freshBrowserSigninStillToVerify': True}, indent=2))
    print('Open https://zectip.app/account, sign out, then sign in with X to verify the reduced permissions and existing wallet.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('User sign-in update stopped: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__))
        raise SystemExit(1)
