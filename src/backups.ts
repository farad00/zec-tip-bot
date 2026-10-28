import { backup, DatabaseSync } from 'node:sqlite';
import { mkdirSync, existsSync, realpathSync, statSync, copyFileSync, constants, chmodSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
const REQUIRED=['settings','users','payments','attempts','usage','leases'];
export function inspectBackup(path:string) {
  if(!existsSync(path)||!statSync(path).isFile())throw new Error('Backup file was not found.');
  const db=new DatabaseSync(path,{readOnly:true});
  try {
    const journal=db.prepare('PRAGMA journal_mode').get() as {journal_mode:string};
    if(journal.journal_mode!=='delete')throw new Error('Use a standalone online-backup snapshot, not a live WAL database.');
    const integrity=db.prepare('PRAGMA integrity_check').get() as {integrity_check:string};
    if(integrity.integrity_check!=='ok')throw new Error('Database integrity check failed.');
    const names=db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all().map(row=>row.name);
    if(REQUIRED.some(name=>!names.includes(name)))throw new Error('This is not a complete bot-state backup.');
    return {ok:true,initialized:Boolean(db.prepare("SELECT 1 FROM settings WHERE key='since_id'").get()),requests:Number(db.prepare('SELECT COUNT(*) AS n FROM payments').get()?.n),unresolved:Number(db.prepare("SELECT COUNT(*) AS n FROM attempts WHERE status!='sent'").get()?.n)};
  } finally {db.close();}
}
export async function snapshotState(source:string,destination:string) {
  if(!existsSync(source)||!statSync(source).isFile())throw new Error('Source state file was not found.');
  if(resolve(source)===resolve(destination)||existsSync(destination))throw new Error('Backup destination already exists or matches the source.');
  mkdirSync(dirname(destination),{recursive:true,mode:0o700});
  // SQLite online backup includes committed WAL data. Never copy a live database file by itself.
  writeFileSync(destination, '', {flag:'wx',mode:0o600}); // claim this new output atomically
  const db=new DatabaseSync(realpathSync(source),{readOnly:true});
  try {await backup(db,destination);} finally {db.close();}
  const standalone=new DatabaseSync(destination);
  try {standalone.exec('PRAGMA journal_mode=DELETE');} finally {standalone.close();}
  chmodSync(destination,0o600);
  return inspectBackup(destination);
}
export function restoreState(source:string,destination:string) {
  const report=inspectBackup(source);
  if(existsSync(destination)||existsSync(destination+'-wal')||existsSync(destination+'-shm'))throw new Error('Restore destination must be empty. Stop the services and preserve the previous state directory first.');
  mkdirSync(dirname(destination),{recursive:true,mode:0o700});
  copyFileSync(source,destination,constants.COPYFILE_EXCL);chmodSync(destination,0o600);
  return report;
}
