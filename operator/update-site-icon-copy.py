#!/usr/bin/env python3
"""Use the existing favicon on private pages and apply the requested copy edits."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

ARCHIVE=Path('/root/zec-site-icon-copy-r2.zip')
ARCHIVE_SHA='b6e5a30ef444aa6ea633963284ff28c7b193ae7a2ffdf3682a6c2eea8df8d32f'
HELPER_SHA='2914c6a38df74497883ab7adb3300851999523cc57badc7e98df1cb1c47ba4bf'
FILES={'src/account.ts','src/account-ui.ts','test/web.test.ts'}
PROBE='''import {request} from "node:http";
import {loadConfig} from "./dist/src/config.js";
const config=loadConfig();
if(!config.privateTips||config.network!=="testnet")process.exit(1);
const read=path=>new Promise((resolve,reject)=>{
 const req=request({hostname:"127.0.0.1",port:3001,path,headers:{host:new URL(config.baseUrl).host}},res=>{
  let body="";res.setEncoding("utf8");res.on("data",chunk=>body+=chunk);
  res.on("end",()=>resolve({status:res.statusCode,headers:res.headers,body}));
 });req.setTimeout(4000,()=>req.destroy(new Error("Local page check timed out")));req.on("error",reject);req.end();
});
for(const path of ["/","/account"]){
 const page=await read(path);
 if(page.status!==200||!page.body.includes('href="/favicon.svg?v=2"')||page.body.includes("The operator knows your X-to-wallet association and controls hosted funds."))process.exit(1);
}
const icon=await read("/favicon.svg?v=2");
if(icon.status!==200||icon.headers["content-type"]!=="image/svg+xml"||!icon.body.startsWith("<svg"))process.exit(1);
'''


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    os.umask(0o077)
    path=Path('/root/update-visible-settings.py');info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid!=0 or info.st_mode&0o022 or hashlib.sha256(path.read_bytes()).hexdigest()!=HELPER_SHA:
        raise ValueError('Expected the verified runtime-preserving website updater.')
    spec=importlib.util.spec_from_file_location('site_icon_visible',path)
    visible=importlib.util.module_from_spec(spec);spec.loader.exec_module(visible)
    base=visible.load(Path('/root/update-dashboard.py'),'site_icon_base',visible.BASE_SHA)
    health=visible.load(Path('/root/enable-testnet-deposits.py'),'site_icon_health')
    idle=visible.load(Path('/root/update-confirming-balance.py'),'site_icon_idle')
    visible.IDLE=idle.IDLE
    original_compose=base.compose
    def compose(*args,**kwargs):
        if args[:1]==('up',):
            running=original_compose('ps','--status','running','-q','bot').stdout.strip()
            if running:original_compose('exec','-T','bot','node','--input-type=module','-e',idle.IDLE)
            else:original_compose('run','--rm','--no-deps','bot','node','--input-type=module','-e',idle.IDLE)
            idle.native_idle()
        return original_compose(*args,**kwargs)
    base.compose=compose
    original_wait=base.wait_web
    def wait_web():
        original_wait()
        original_compose('exec','-T','bot','node','--input-type=module','-e',PROBE)
    base.wait_web=wait_web
    visible.ARCHIVE=ARCHIVE;visible.ARCHIVE_SHA=ARCHIVE_SHA;visible.FILES=FILES
    idle.native_idle()
    report=visible.install(base,health)
    print(json.dumps({'siteIconAndCopyUpdated':True,'existingLogoUsed':True,'highlightedFooterParagraphRemoved':True,
        'consentWordingUpdated':True,'signupConsentShortened':True,'network':'testnet','workerConfigurationPreserved':report['workerConfigurationPreserved'],
        'transfersEnabled':report['transfersEnabled'],'walletSettingsChanged':False,'domainConfigurationChanged':False,
        'walletOrNodeRestarted':False,'paymentsRequestedByInstaller':False},indent=2))
    print('Refresh zectip.app. If the tab icon is cached, close the tab and open the site again.')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Site icon update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
