#!/usr/bin/env python3
"""Diagnose one public command without posting, enqueueing, signing or revealing financial data."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
CODE='''import {loadConfig} from "./dist/src/config.js";import {State} from "./dist/src/state.js";import {XApi} from "./dist/src/x.js";
const c=loadConfig(),state=new State("/app/data/bot.sqlite"),id=process.argv[1];
const report={network:c.network,mode:process.env.BOT_MODE,privateMode:c.privateTips,walletWorker:c.hostedWallets,postingSwitch:c.posting,workerHeld:!!state.get("worker_hold"),postsOrPaymentsRequested:false};
try{
const profile=state.db.prepare("SELECT user_id,enabled,enabled_after,default_zat IS NOT NULL AND daily_zat IS NOT NULL AND fee_zat IS NOT NULL AS configured FROM tip_profiles WHERE username='farad_0' AND network='testnet'").get();
report.senderProfileFound=!!profile;report.senderTipEnabled=profile?.enabled===1;report.senderPresetConfigured=!!profile?.configured;
report.senderOptedOut=profile?!!state.user(profile.user_id)?.opted_out:null;
const cursor=state.get("since_id");report.cursorHasPassedCommand=!!(cursor&&/^\\d+$/.test(cursor)&&BigInt(cursor)>=BigInt(id));
report.commandNewerThanSettings=profile?Number((BigInt(id)>>22n)+1288834974657n)>=profile.enabled_after:null;
report.commandAgeSeconds=Math.floor((Date.now()-Number((BigInt(id)>>22n)+1288834974657n))/1000);
report.existingJob=state.db.prepare("SELECT status,reason FROM tip_jobs WHERE source=?").get(id)??null;
const api=new XApi(c,cost=>state.reserveRequest(cost,c.budgetMills));
try{
 const result=await api.request("GET",`/2/tweets/${id}?tweet.fields=author_id,created_at,entities,referenced_tweets`,5),post=result.data;
 report.xReadSucceeded=!!post;report.sourceAuthorMatches=!!(post&&profile&&post.author_id===profile.user_id);
 if(post){report.exactSupportedTip=/^@zectipbot\\s+tip(?:\\s+@[A-Za-z0-9_]{1,15})?$/i.test(post.text.trim());
 report.botMentionEntityMatches=!post.entities?.mentions?.length||post.entities.mentions.some(m=>m.id===c.botId||m.username?.toLowerCase()==='zectipbot');
 const parents=post.referenced_tweets?.filter(p=>p.type==='replied_to')??[];report.replyParents=parents.length;
 if(parents.length===1){const recipient=await api.postAuthor(parents[0].id);report.recipientIsBot=recipient.id===c.botId;report.recipientWalletReady=state.db.prepare("SELECT status FROM tip_wallets WHERE user_id=? AND network='testnet'").get(recipient.id)?.status==='ready';report.recipientOptedOut=!!state.user(recipient.id)?.opted_out;}}
}catch(error){report.xReadSucceeded=false;report.xHttpStatus=Number.isInteger(error?.status)?error.status:null;report.xReadFailureKind=error?.constructor?.name??"unknown";}
console.log(JSON.stringify(report));
}finally{state.close();}'''

def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    if len(sys.argv)!=2 or not re.fullmatch(r'[0-9]{1,20}',sys.argv[1]):raise ValueError('Supply the public source post ID only.')
    result=subprocess.run(['docker','compose','--env-file','.env','-f','deploy/compose.yaml','exec','-T','bot','node','--input-type=module','-e',CODE,sys.argv[1]],cwd=Path('/srv/zec-tip-bot'),check=True,capture_output=True,text=True,timeout=60)
    report=json.loads(result.stdout)
    logs=subprocess.run(['docker','compose','--env-file','.env','-f','deploy/compose.yaml','logs','--no-color','--tail','60','bot'],cwd=Path('/srv/zec-tip-bot'),capture_output=True,text=True,timeout=20)
    categories={'Daily spending or reply limit reached':'daily_x_guard','X access requires attention':'x_access_hold','X rate limit reached':'x_rate_limit',
                'Another bot command holds':'worker_lease_busy','Automatic replies enabled':'x_worker_started','Automatic worker is held':'fatal_hold',
                'Automatic checking stopped after an error':'worker_error_hold'}
    report['workerLogCategories']=[label for text,label in categories.items() if text in logs.stdout]
    report['xReadsConsumeCredits']=True
    print(json.dumps(report,indent=2))
if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Private tip diagnostic stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
