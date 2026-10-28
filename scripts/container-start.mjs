import { deploymentReport } from '../dist/src/deployment.js';
const report=deploymentReport(process.env);
if(!report.ok) {
 for(const check of report.checks.filter(c=>!c.ok))console.error('Deployment configuration: '+check.name+' - '+check.detail);
 process.exitCode=1;
} else {
 process.argv=[process.execPath,'dist/src/cli.js',process.env.BOT_MODE??'serve'];
 await import('../dist/src/cli.js');
}
