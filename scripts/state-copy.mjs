import { join } from 'node:path';
import { snapshotState,inspectBackup,restoreState } from '../dist/src/backups.js';
const [action,file]=process.argv.slice(2);
try {
 if(!['backup','check','restore'].includes(action)||!file)throw new Error('Use state-copy.mjs backup|check|restore PATH.');
 const source=join(process.env.DATA_DIR??'data','bot.sqlite');
 const report=action==='backup'?await snapshotState(source,file):action==='check'?inspectBackup(file):restoreState(file,source);
 console.log(JSON.stringify({action,...report}));
 console.log('No X or wallet requests were made. Credentials and addresses were not printed.');
} catch {
 console.error('State copy could not finish. Check the action, source file and unused destination. Restore never overwrites an existing database, WAL or shared-memory file.');process.exitCode=1;
}
