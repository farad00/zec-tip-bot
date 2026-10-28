# Private default-tip preview — 28 October 2026

## Implemented

Separate Sign in with X verifies the numeric X ID and discards user access tokens after verification. It does not overwrite the bot credentials. One-hour sessions use HttpOnly/SameSite cookies, Secure on HTTPS, with origin/CSRF checks and callback replay protection. Settings and history are account-scoped.

Each user explicitly chooses a default amount, daily cap including reserved fees, fee ceiling and hosted-spending consent. The app never chooses an amount for them. Exact commands are @zectipbot tip under a post, which resolves the immediate parent author, and @zectipbot tip @recipient, which resolves the current numeric X ID. Quotes, thread-root assumptions, prose and public amounts do not authorize a tip.

Commands have durable per-post deduplication, immutable accepted amounts, stale-command rejection, atomic daily reservations and private failure status. Stop disables tipping without deleting hosted wallet access; start does not re-enable spending. Public acknowledgements are generic and off by default.

Private mode disables public registration and legacy payment pages/cards. The original public comment still reveals tipping intent. An adapter-tested state machine checks fees/privacy metadata, persists signed bytes, never blindly retries uncertain signing, rebroadcasts the same transaction after timeouts and distinguishes submission, confirmation and reorganization.

## Unavailable

There is no connected wallet adapter. Account provisioning, chain-backed deposits/balances, real signing/broadcast integration, withdrawals and recovery acceptance tests remain outstanding. Tests use a synthetic wallet only; the application never substitutes fake balances or transaction IDs. No live app path provisions a ready wallet, so enabling spending remains unavailable. Do not fund this preview.

Mainnet hosted transfers are blocked. Testnet is our integration stage, not a competition requirement. Mainnet remains the desired final target after actual end-to-end verification.

## Apply and preview

Stop the old bot watcher, then stop the payment server in its own terminal. The tunnel can keep running. In a new terminal in the existing project, run the delivered apply-private-tipping-update.mjs script, then npm.cmd run private:setup. The updater backs up changed code and builds; it does not read credentials/data or contact X. The separate setup atomically updates non-secret mode, network, data-directory and callback switches while preserving credentials. It disables posting/cards and uses a new ignored data-test-private directory, preserving old data.

Setup prints an additional callback ending in /account/callback. Add that EXACT URL to the EXISTING X app's allowed OAuth 1.0a callbacks, keeping the bot's loopback callback as well. Do not regenerate or share credentials. A tunnel hostname change requires updating this callback; stable hosting avoids repeated changes.

Restart npm.cmd run serve in the PAYMENT SERVER terminal. Open the printed /account URL, sign in as a personal X account and save a default, daily cap and fee ceiling. Spending stays disabled. Do not restart the old public-mode watcher. Sign-in consumes X API credits; avoid repeated unnecessary authentication.

## Server integration

The paid VPS is provisioning. Verify ordered resources and disk before deploying the website with posting disabled. The current server template selects private Testnet mode. Do not import public registrations as private hosted-wallet consent.

Run a pinned wallet with compatible chain services, encrypted recovery material and private RPC. Validate actual release capabilities with rpc.discover; latest documentation alone does not establish what an installed release supports. Official RPC reference: https://zcash.github.io/zallet/rpc/index.html

The wallet:probe command needs Node 24 in the same loopback namespace as the wallet. Enter RPC settings privately. It reports capability names only, not network, balances, recovery or readiness to spend. The bot container cannot reach host loopback without an intentionally designed adapter; never expose RPC publicly to work around that isolation.

The adapter must provision isolated accounts idempotently, reconcile interruptions, verify sync/network, shielded receiving addresses and actual inputs, outputs, change and fees under FullPrivacy. App contract metadata checks are not cryptographic transaction validation. Serialize wallet operations with a durable process lease; an in-process guard is insufficient across processes. Do not connect automatic signing until deposit, tip, withdrawal, duplicate-command, fault and recovery checks pass.

## Verification and privacy boundary

76 offline tests passed. These use synthetic credentials and a test wallet, without paid X calls or real funds. The socket-based HTTP test is excluded because this environment denied binding; handlers are tested directly, including OAuth, ownership, CSRF and public redaction. Cloud deployment, Docker build, browser sign-in and real Testnet transactions remain unverified.

X sees comments/sign-in, and the operator knows account mappings and controls hosted spending. No anonymity-from-operator claim is made. Organisers still need to clarify whether the intentional public tipping signal satisfies the leak rule. Earlier public posts and copies cannot be recalled by this upgrade.

