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

Keep operator Testnet transfer controls, per-user caps, global reservations and paid X access limits in place. Do not delete pending/review payment records or rewind the mention cursor to repeat an uncertain command. Recovery from a stale backup starts with workers held and profiles disabled.
