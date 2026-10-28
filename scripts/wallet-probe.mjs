import {probeWallet} from '../dist/src/wallet-probe.js';
try { console.log(JSON.stringify(await probeWallet(process.env),null,2)); }
catch { console.error('Wallet capability check could not finish. Check local RPC settings. Credentials and upstream responses were not printed.');process.exitCode=1; }
