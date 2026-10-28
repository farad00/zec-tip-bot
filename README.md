# ZEC Tip Bot

Tip from an X comment. Choose the amount privately.

This is a **hosted Zcash Testnet pilot**. Sign in with X, create a wallet linked to your verified numeric X ID, fund it, and choose a default tip and spending limits. Reply under a post:

```text
@zectipbot tip
```

The immediate parent post's author is the recipient. Alternatively:

```text
@zectipbot tip @recipient
```

Amounts in public commands are rejected. Both accounts need linked wallets. `stop` disables automatic tipping; `start` does not re-enable spending. The sender must explicitly enable tips in their private settings again.

## Current integration status

As of 28 October 2026:

- Deployed behind HTTPS with separate OAuth 1.0a sign-in for users and a pinned @zectipbot identity.
- Two native Testnet accounts created and linked to separate X identities.
- Encrypted off-server recovery archive verified, including native seed recovery and matching account mappings. Native backup confirmation completed. A full native restore rehearsal has **not** been completed.
- A real shielded Testnet deposit is confirmed and spendable.
- A native unsigned version-6 NU7 proposal passed recipient, amount, fee, branch and FullPrivacy checks, with no transparent inputs or outputs. That check did not sign or broadcast a transaction.
- Private withdrawals and wallet processing are enabled for the two-account pilot. A native withdrawal was signed, broadcast and confirmed; the user confirmed it appeared in the independent receiving wallet.
- A fresh X reply triggered an automatic native Testnet tip, which reached 24 chain confirmations. The recipient saw the incoming balance, withdrew part of it, and confirmed receipt in the independent Zingo Testnet wallet; that withdrawal reached 6 confirmations. This verifies deposit, comment tip, recipient credit and external withdrawal on the live pilot.
- Two earlier tip attempts were refused before signing because their saved fee limits were too low. They remain recorded as not sent and were not replayed.
- Automatic X polling is enabled after @zectipbot verification and initialization of a fresh command window. User spending remains a separate opt-in. Hosted Mainnet transfers are blocked in code.

The application shows real availability, confirmed balances, and durable payment state. An unavailable balance is not displayed as zero. No simulated balance or transaction ID is substituted into the deployed application.

Incoming tips appear in the recipient's private activity. Recent outside deposits have separate Received/Confirming entries from an account-scoped native history query. Change outputs and known bot transfers are excluded from the deposit list. Memos, receiving addresses and native transaction identifiers are not rendered in activity. The optional public reply acknowledges command receipt with a generic account link; it does not disclose payment success or failure.

## Privacy boundary

Shielded transaction contents are private from the public. Public X comments still reveal tipping intent, interactions and timing. X sees authentication and commands. **The operator knows the X-to-wallet mapping and controls hosted funds.** This is not anonymous social activity or self-custody.

Private mode disables legacy public registration, payment-request pages and financial reply cards. Amounts, receiving addresses and transaction status are shown only after owner-scoped sign-in. Public acknowledgements are optional, generic, and off by default. Never include a seed phrase, private key, private financial screenshot, or receiving address in a demo or public issue.

## Fees and pilot limits

The bot service fee is zero. Zcash network fees apply; the user sets a maximum additional fee. Daily reservations include the fee ceiling. X API access separately consumes the operator's developer credits and has its own spending guard.

The Testnet broker limits each payment to 0.01 TAZ, its fee ceiling to 0.001 TAZ, and total daily reservations to 0.1 TAZ. Deposits require 10 confirmations to be spendable; transfers are marked confirmed at 3 confirmations. Testnet coins have no monetary value.

The funded pilot currently has two backed-up accounts. New wallet enrollment is held until the operator completes another account-mapping backup window; existing accounts can continue to receive and transfer. A fee above the sender's saved ceiling is refused before signing and shown privately as not sent. Changing a preset applies only to new commands.

## Run and test locally

Requires Node.js 24 or newer.

```sh
npm ci --ignore-scripts
npm run setup
npm run build
npm test
```

Use `.env` locally for the existing X application's credentials. It is ignored by Git. Never paste credentials into chat or commit them. `npm run auth` verifies @zectipbot before saving its bot access token. User sign-in verifies each person's numeric X ID separately and discards their access credentials after the callback.

The `.env.example` starts in private Testnet mode with posting, account provisioning and transfers disabled. A website-only local preview does not create a working wallet backend. The isolated native broker and verified recovery are required for native account provisioning and transfers.

See [PRIVATE-TIPPING.md](PRIVATE-TIPPING.md) for configuration and [deploy/WALLET-BACKEND.md](deploy/WALLET-BACKEND.md) for the native boundary and pinned source contract. Deployment uses:

```sh
docker compose --env-file .env -f deploy/compose.yaml ps
```

`BOT_MODE=serve` serves account pages; with the hosted-wallet switch enabled it also processes already authorized wallet jobs. `BOT_MODE=start` additionally starts X polling. Do not enable it until identity, spending limits, a fresh command window and the funded transaction path have been checked.

## Architecture

```text
X OAuth / explicit mention
          |
Private Node/TypeScript website and durable SQLite jobs
          |
Fixed local Unix socket — no wallet keys or native database in the web container
          |
Restricted Python broker — operator controls, proposal validation, durable signing state
          |
Local Zallet wallet + NU7-compatible Zebra Testnet node/indexer
```

Commands are deduplicated by source post ID. Edited tip posts do not authorize new transfers. Verified X reply metadata handles hidden conversation mentions while the immediate parent still determines the recipient. Accepted amounts and fee reservations are immutable. Preparation/signing uncertainty is held for review; it is never retried by creating a new spend. Signed bytes are stored before broadcast, and broadcast recovery uses those exact bytes. Confirmations and reorganization are distinguished.

Tests cover identity pinning, OAuth replay/CSRF, owner isolation, exact command parsing, stale commands, caps, duplicate handling, interrupted signing, identical-byte broadcast recovery, wallet leases, confirmation/reorganization and private UI redaction. Unit tests alone do not prove a funded chain transfer; live integration evidence is recorded separately.

## Earlier prototype

[LEGACY-PAYMENT-REQUESTS.md](LEGACY-PAYMENT-REQUESTS.md) preserves the original noncustodial ZIP-321 request prototype. Its commands, custody statements and Mainnet examples do not describe the private hosted-wallet pilot. It is not the submission's active flow.
