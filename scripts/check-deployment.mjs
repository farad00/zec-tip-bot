import { deploymentReport } from '../dist/src/deployment.js';
const report=deploymentReport(process.env);
for(const c of report.checks) console.log((c.ok?'OK: ':'FIX: ')+c.name+' - '+c.detail);
console.log('Offline configuration check only. No values, credentials, X calls, wallet calls, DNS checks, posts or purchases.');
if(!report.ok)process.exitCode=1;
