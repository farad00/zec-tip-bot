# Operator build tools

These are the public scripts used during the Testnet integration. They contain source and fixed public checksums, not recovery data. Historical installers apply to the already-prepared staged deployment; they are not a one-command fresh production installation. The canonical current application is at the repository root.

Only run Linux root helpers after reviewing their fixed paths and prerequisites. The local recovery verifiers run on the operator PC and must never be given to a web container. Do not commit their output archives, recovery words, private keys, environment files, wallet databases or account mapping snapshots.

The releases in releases/ contain allowlisted public source snapshots needed by the historical installers. Their checksums are pinned in those scripts. Other ZIP/backup files remain ignored. Existing services and recovery state must be preserved.
