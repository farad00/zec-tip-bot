# Deploy the private Testnet pilot

The running architecture uses a Node.js website/worker, a private Unix-socket broker, a native Zallet wallet and an indexer-enabled Zebra Testnet node. [WALLET-BACKEND.md](WALLET-BACKEND.md) records source pins, native RPC checks and recovery limitations.

## Website

Use a persistent Linux server with Docker Compose, a stable HTTPS hostname, and disk space for the node database. The web application is private behind Caddy; only ports 80 and 443 are published. Persist `/app/data` across container rebuilds. Do not run a second worker against a copied database.

Copy `host.env.example` to a private server `.env` and enter credentials locally. Set `DOMAIN`, `PUBLIC_BASE_URL` and the existing app's bot credentials. Register the additional exact `PUBLIC_BASE_URL/account/callback` in the X app. Use `PRIVATE_TIPPING_ENABLED=true` and `ZCASH_NETWORK=testnet`.

```sh
docker compose --env-file .env -f deploy/compose.yaml build bot
docker compose --env-file .env -f deploy/compose.yaml run --rm --no-deps bot node scripts/check-deployment.mjs
docker compose --env-file .env -f deploy/compose.yaml up -d
docker compose --env-file .env -f deploy/compose.yaml ps
```

Keep `BOT_MODE=serve`, `X_POSTING_ENABLED=false`, `WALLET_ACCOUNTS_ENABLED=false`, and `HOSTED_WALLETS_ENABLED=false` for the initial website-only stage. `/healthz` proves web liveness only.

## Live domain

The live app is at https://zectip.app. Both `www.zectip.app` and the original submitted hostname, `178-18-247-12.sslip.io`, redirect to it. Existing users sign in again on the new hostname; verified numeric X IDs still map to their existing wallets.

`operator/migrate-zectip-domain.py` upgrades this specific deployment. It requires the root domain and `www` to resolve directly to the existing VPS, and the operator must first add `https://zectip.app/account/callback` to the existing X app without deleting previous callbacks. Run without flags for a read-only check. `--apply --callback-registered` prepares HTTPS, checks active work, preserves a protected configuration backup and changes only the domain settings and proxy routes. It does not move wallet data or restart the native wallet/node.

Old GET/HEAD links retain their paths and query strings, except OAuth callback parameters, which are dropped before returning to sign-in. Old POST requests receive a 409 response and are not redirected or replayed. Configuration and runtime checks preserve the workers and spending controls. A routing-only retry on an already switched origin does not restart workers. Review a stopped or rolled-back migration before retrying; do not delete its backup to bypass that check.

## Native backend

Run the pinned NU7-compatible node/indexer and wallet as separate host services. Keep all RPC listeners on loopback. The broker is the only application interface; the web container receives a read-only mount of the fixed broker socket directory and the numeric client group. It must not receive native wallet files, encryption identities, recovery archives or native RPC credentials.

Initialize encrypted operator recovery, verify the encrypted backup off-server, and complete native backup confirmation before provisioning accounts. User accounts use opaque native labels linked to immutable verified X IDs. The backed-up native account set and application/broker mappings must match before funding is enabled.

For the current pilot, those checks passed for two linked accounts. A real deposit became spendable and a native withdrawal was confirmed and observed in the separate receiving wallet. This evidence does not complete a full native restore rehearsal or a hosted Mainnet safety review.

## Enable in stages

1. Enable account creation through the fixed broker, without deposits or transfers.
2. Capture and verify a matching encrypted snapshot of the native wallet, application mappings and broker state. Enable private receiving addresses only after that verification.
3. Verify a funded unsigned proposal under FullPrivacy. Enable the private wallet worker while X processing stays off. Confirm a small manual withdrawal in an independent Testnet wallet.
4. Verify @zectipbot, keep automatic profiles off, establish a fresh mention window, then start X polling. Users explicitly enable private presets before posting new tip comments.

`BOT_MODE=serve` can run the private wallet worker when `HOSTED_WALLETS_ENABLED=true`; it still does not poll X. `BOT_MODE=start` starts X polling as well. Public acknowledgements remain generic and are off by default.

Keep operator Testnet transfer controls, per-user caps and global reservations in place. X billing controls are independent. Do not delete pending/review payment records or rewind the mention cursor to repeat an uncertain command. Recovery from a stale backup starts with workers held and profiles disabled.

## External Testnet enrollment

The later enrollment adapter preserves the originally backed-up accounts while separately admitting new bindings. The native account limit is 16. Before exposing a new receiving address, it encrypts a consistent native-wallet and broker snapshot plus the X-ID binding to the operator's previously verified public recovery recipient. It does not copy the wallet encryption identity into these new snapshots. The original verified off-server recovery set is still required.

`operator/install-public-enrollment.py` upgrades the existing source-pinned pilot and configures the approved sponsor. It runs the broker tests, builds the website, checks pending work, and briefly pauses the website/broker. It does not restart the wallet/node or create accounts. This is an upgrade for that deployment, not a general fresh-server installer.

Apply `operator/fix-enrollment-address-policy.py` after the initial enrollment installer. New setups use the fixed shielded diversifier index 4294967296 to avoid a native default address occupying index zero with different receiver types. Existing stored receiving addresses are unchanged. The native source, not an error-number guess, identified this collision during integration.

Welcome grants are 0.003 TAZ, with a maximum fee of 0.001 TAZ and a lifetime limit of 12 grants. The sponsor must have the entire 0.048 TAZ budget available at installation. Request keys are bound to numeric X IDs, so repeat callbacks and process restarts cannot create a second grant. Signing uncertainty is held; broadcast recovery uses identical stored bytes.

Run `operator/check-public-enrollment.py` on the server for aggregate readiness. Use `operator/export-enrollment-backups.py` to bundle encrypted snapshots, copy that bundle off the VPS, and run `operator/verify-enrollment-backups-local.py --sha256 EXPECTED_HASH` privately on the operator PC. New snapshots are not automatically off-server. Never resume a restored snapshot with automatic profiles enabled or an old mention cursor.

`operator/update-confirming-balance.py` installs separate pending-balance display and selects `minimumConfirmations: 3` in the protected Testnet transfer controls. Defaults remain 10. Only 3 and 10 are supported; zero-confirmation spending is rejected. The updater checks active website/native work before restarting the broker, preserves all payment records, and restores the previous source/policy on a failed probe. It does not restart Zebra or Zallet. The grants, receipt labels, displayed spendable balance and `pczt_create` input minimum all use this setting. Refresh the owner page to see updated values.
