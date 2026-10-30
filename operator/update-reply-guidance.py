#!/usr/bin/env python3
"""Clarify command setup guidance while keeping payment outcomes private."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

ARCHIVE_SHA = 'ddfde6a4a2edcc92cae636a1254a44c5a6c81bcfdd57b8cc852451ff95df2076'
HELPER_SHA = '361110a0c980ffcd969d0138554304a01dc5e2cef7c393400a1fc841c83073bc'
FILES = {'src/private-tips.ts', 'test/private-reply-context.test.ts', 'README.md', 'deploy/README.md'}
PROBE = '''import {loadConfig} from "./dist/src/config.js";
import {State} from "./dist/src/state.js";
import {TipStore} from "./dist/src/tip-store.js";
import {currentSnowflake,planMention} from "./dist/src/bot.js";
const c=loadConfig();if(!c.privateTips||c.network!=="testnet")process.exit(1);
// Synthetic in-memory records only; no real identity, wallet, X call or payment.
const state=new State(":memory:"),store=new TipStore(state);
const mention={id:currentSnowflake(),author_id:"200",text:"@zectipbot tip @recipient",entities:{mentions:[{username:"zectipbot",id:c.botId}]}};
const api={lookup:async()=>({id:"300",username:"recipient"})};
try{
 const fresh=await planMention(mention,c,state,api);
 if(fresh?.text!=="Configure and enable tipping first: "+c.baseUrl+"/account"||store.job(mention.id))process.exit(1);
 store.login("200","probe","testnet");
 const incomplete=await planMention(mention,c,state,api);if(incomplete?.text!==fresh.text)process.exit(1);
 state.db.prepare("UPDATE tip_profiles SET enabled=1,default_zat=1,daily_zat=3,fee_zat=1,public_ack=1 WHERE user_id='200'").run();
 const ready=await planMention(mention,c,state,api);
 if(ready?.text!=="Command received. Check your private wallet for status: "+c.baseUrl+"/account"||store.job(mention.id))process.exit(1);
 state.opt("200",true);if(await planMention(mention,c,state,api)!==undefined)process.exit(1);
}finally{state.close();}
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
    root = Path('/srv/zec-tip-bot')
    unchanged = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ('src/account.ts', 'src/user-auth.ts', 'src/config.ts', 'src/auth.ts', 'src/x.ts')}
    spec = importlib.util.spec_from_file_location('reply_guidance_base', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ARCHIVE = Path('/root/zec-reply-guidance.zip')
    module.ARCHIVE_SHA = ARCHIVE_SHA
    module.FILES = FILES
    module.PROBE = PROBE
    module.main()
    if any(hashlib.sha256((root / name).read_bytes()).hexdigest() != digest for name, digest in unchanged.items()):
        raise ValueError('Authorization sources changed unexpectedly; inspect the deployment.')
    print(json.dumps({'replyGuidanceUpdated': True, 'setupGuidanceForNewAndDisabledSenders': True,
        'normalAcknowledgementSignupTextRemoved': True, 'recipientRegistrationStatusRemainsPrivate': True,
        'paymentOutcomesRemainPrivate': True, 'explicitStopStillSuppressesReplies': True,
        'userSigninAndBotAuthorizationUnchanged': True, 'walletSettingsAndGrantBudgetChanged': False,
        'historicalCommandsReplayed': False, 'xCallsOrPaymentsRequestedByInstaller': False,
        'nativeServicesChanged': False}, indent=2))
    print('Only fresh commands use the new replies. The installer did not send a tip or replay an earlier command.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Reply guidance update stopped: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__))
        raise SystemExit(1)
