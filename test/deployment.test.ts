import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,readFileSync,writeFileSync,existsSync} from 'node:fs';
import {join} from 'node:path';
import {deploymentReport} from '../src/deployment.js';
import {loadConfig} from '../src/config.js';
import {State} from '../src/state.js';
import {snapshotState,inspectBackup,restoreState} from '../src/backups.js';
const valid=()=>({DOMAIN:'tips.myownsite.io',PUBLIC_BASE_URL:'https://tips.myownsite.io',DATA_DIR:join(process.cwd(),'persistent-data'),WEB_HOST:'0.0.0.0',BOT_MODE:'serve',X_POSTING_ENABLED:'false'});

test('deployment preflight validates stable origin and persistence without exposing values or making network calls',()=>{
 assert.equal(deploymentReport(valid()).ok,true);
 for(const change of [{PUBLIC_BASE_URL:'http://localhost:3001'},{PUBLIC_BASE_URL:'https://temporary.trycloudflare.com'},{PUBLIC_BASE_URL:'https://tips.example.org'},{DOMAIN:'bad { syntax'},{DOMAIN:'another.site.io'},{DATA_DIR:'data'},{BOT_MODE:'auth'},{HOSTED_WALLETS_ENABLED:'true'}])assert.equal(deploymentReport({...valid(),...change}).ok,false);
 const report=deploymentReport({...valid(),BOT_MODE:'start',X_POSTING_ENABLED:'true',X_API_KEY:'DO_NOT_PRINT',X_API_SECRET:'DO_NOT_PRINT',X_ACCESS_TOKEN:'DO_NOT_PRINT',X_ACCESS_SECRET:'DO_NOT_PRINT',X_BOT_USER_ID:'100'});
 assert.equal(report.ok,true);assert.ok(!JSON.stringify(report).includes('DO_NOT_PRINT'));
 assert.equal(deploymentReport({...valid(),BOT_MODE:'start',X_POSTING_ENABLED:'true'}).ok,false);
});

test('host port fallback and explicit persistent-state path preserve local defaults',()=>{
 assert.equal(loadConfig({}).webPort,3001);assert.equal(loadConfig({}).dataDir,'data');
 assert.equal(loadConfig({PORT:'10000'}).webPort,10000);assert.equal(loadConfig({PORT:'10000',WEB_PORT:'3001'}).webPort,3001);
 assert.throws(()=>loadConfig({PORT:'not-a-number'}));assert.throws(()=>loadConfig({DATA_DIR:''}));assert.throws(()=>loadConfig({DATA_DIR:'bad\0path'}));
});

test('online backup includes committed WAL state and restored cursor and uncertain attempts survive a restart',async()=>{
 const dir=mkdtempSync(join(process.cwd(),'data-test-snapshot-'));const source=join(dir,'live','bot.sqlite'),snapshot=join(dir,'snapshots','state.sqlite'),restored=join(dir,'restored','bot.sqlite');
 const s=new State(source);
 try{
  s.set('since_id','100');s.register('200','PRIVATE_ADDRESS');s.reserveReply('101',10);s.uncertain('101');s.reserveCost(100,1000);
  assert.ok(existsSync(source+'-wal'));assert.throws(()=>inspectBackup(source),/standalone/);
  const report=await snapshotState(source,snapshot);assert.equal(report.ok,true);assert.equal(report.unresolved,1);assert.ok(!JSON.stringify(report).includes('PRIVATE_ADDRESS'));
  s.set('since_id','999');assert.equal(restoreState(snapshot,restored).ok,true);
  const recovered=new State(restored);try{assert.equal(recovered.get('since_id'),'100');assert.equal(recovered.attempted('101'),true);assert.equal(recovered.user('200')?.address,'PRIVATE_ADDRESS');}finally{recovered.close();}
  await assert.rejects(snapshotState(source,snapshot),/exists/);assert.throws(()=>restoreState(snapshot,source),/empty/);
  const sidecar=join(dir,'sidecar.sqlite');writeFileSync(sidecar+'-wal','existing');assert.throws(()=>restoreState(snapshot,sidecar),/empty/);
  assert.equal(readFileSync(sidecar+'-wal','utf8'),'existing');
 }finally{s.close();rmSync(dir,{recursive:true,force:true});}
});

test('backup inspection rejects unrelated SQLite or corrupt files without printing database contents',()=>{
 const dir=mkdtempSync(join(process.cwd(),'data-test-corrupt-'));try{const p=join(dir,'bad.sqlite');writeFileSync(p,'not-sqlite');assert.throws(()=>inspectBackup(p));}finally{rmSync(dir,{recursive:true,force:true});}
});

test('deployment manifest publishes only HTTPS proxy ports, persists state, and starts through the mode guard',()=>{
 const root=new URL('../../',import.meta.url);
 const compose=JSON.parse(readFileSync(new URL('deploy/compose.yaml',root),'utf8').replace(/^\uFEFF/,''));
 assert.deepEqual(Object.keys(compose.services),['bot','proxy']);assert.equal(compose.services.bot.ports,undefined);
 assert.deepEqual(compose.services.proxy.ports,['80:80','443:443','443:443/udp']);
 assert.ok(compose.services.bot.volumes.includes('bot-data:/app/data'));assert.equal(compose.services.bot.environment.DATA_DIR,'/app/data');
 assert.deepEqual(compose.services.bot.command,['node','scripts/container-start.mjs']);assert.equal(compose.services.bot.restart,'unless-stopped');
 const docker=readFileSync(new URL('Dockerfile',root),'utf8');assert.ok(!/^COPY .*\.env/m.test(docker));assert.match(docker,/USER node/);
 const ignore=readFileSync(new URL('.dockerignore',root),'utf8');assert.match(ignore,/^\*\*/m);assert.ok(!ignore.includes('!.env'));
});
