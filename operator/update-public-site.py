#!/usr/bin/env python3
"""Install public metadata, asset caching, HSTS and the command guide."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

ARCHIVE_SHA='990b3a34e2b856665da40d63f24035333138dd158ddd7517ace5f09c0d0103cd'
HELPER_SHA='361110a0c980ffcd969d0138554304a01dc5e2cef7c393400a1fc841c83073bc'
FILES={'src/account.ts','src/account-ui.ts','src/web.ts','src/site-share.ts','test/public-site.test.ts','Dockerfile'}
PROBE='''import {request} from "node:http";
import {readFileSync} from "node:fs";
import {loadConfig} from "./dist/src/config.js";
const c=loadConfig();if(!c.privateTips||c.network!=="testnet"||!c.baseUrl.startsWith("https://"))process.exit(1);
const read=path=>new Promise((resolve,reject)=>{
 const req=request({hostname:"127.0.0.1",port:3001,path,headers:{host:new URL(c.baseUrl).host}},res=>{
  const chunks=[];res.on("data",chunk=>chunks.push(Buffer.from(chunk)));
  res.on("end",()=>resolve({status:res.statusCode,headers:res.headers,body:Buffer.concat(chunks)}));
 });req.setTimeout(4000,()=>req.destroy(new Error("Local public-page check timed out")));req.on("error",reject);req.end();
});
const home=await read("/"),html=home.body.toString();
if(home.status!==200||home.headers["x-robots-tag"]!=="index, follow"||home.headers["strict-transport-security"]!=="max-age=31536000; includeSubDomains")process.exit(1);
if(!html.includes('rel="canonical" href="'+c.baseUrl+'/"')||!html.includes('name="description"')||!html.includes('content="summary_large_image"')||!html.includes("How a tip moves")||!html.includes("If the recipient has no wallet, no tip is sent."))process.exit(1);
const account=await read("/account");
if(account.status!==200||account.headers["cache-control"]!=="no-store"||account.headers["x-robots-tag"]!=="noindex, nofollow"||account.body.toString().includes("og:image")||account.body.toString().toLowerCase().includes("we hold tip balances"))process.exit(1);
for(const path of ["/favicon.svg?v=3","/assets/private.js?v=3","/assets/site-card-v1.png"]){
 const asset=await read(path);if(asset.status!==200||asset.headers["cache-control"]!=="public, max-age=31536000, immutable"||asset.headers["set-cookie"])process.exit(1);
}
const card=await read("/assets/site-card-v1.png");
if(card.headers["content-type"]!=="image/png"||card.body.readUInt32BE(16)!==1200||card.body.readUInt32BE(20)!==630)process.exit(1);
const source=readFileSync("./dist/src/account.js","utf8");
if(!source.includes("I allow this service to send tips when I post a tip command, within these limits.")||!source.includes("Create a Testnet wallet for this verified X account."))process.exit(1);
'''


def main():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Run in the existing VPS root terminal.')
    path=Path('/root/update-zectip-logo.py');info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_uid!=0 or info.st_mode&0o022
            or hashlib.sha256(path.read_bytes()).hexdigest()!=HELPER_SHA):
        raise ValueError('Expected the verified installed website updater.')
    spec=importlib.util.spec_from_file_location('public_site_base',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.ARCHIVE=Path('/root/zec-public-site.zip');module.ARCHIVE_SHA=ARCHIVE_SHA;module.FILES=FILES;module.PROBE=PROBE
    module.main()
    print(json.dumps({'publicSiteUpdated':True,'hstsEnabled':True,'publicHomepageIndexable':True,
        'privateAccountPagesRemainNoindexAndNoStore':True,'brandedSharePreviewReady':True,
        'versionedAssetsCacheable':True,'commandsAndFlowGuideAdded':True,'signInWordingPreserved':True,
        'walletAndGrantSettingsChanged':False,'nativeServicesChanged':False,'paymentsRequestedByInstaller':False},indent=2))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('Public website update stopped: '+(str(error) if isinstance(error,ValueError) else type(error).__name__))
        raise SystemExit(1)
