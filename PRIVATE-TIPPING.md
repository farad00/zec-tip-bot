# Private comment tipping

The current product is a hosted Testnet wallet linked to a verified X ID. See the [README](README.md) for the current verified integration status and privacy boundary. [PRIVATE-PREVIEW-HISTORY.md](PRIVATE-PREVIEW-HISTORY.md) records the earlier account-only preview; its unavailable-feature list is historical.

## Commands

- `@zectipbot tip` as a reply tips the immediate parent post's author.
- `@zectipbot tip @recipient` resolves the recipient's current numeric X ID.
- `@zectipbot stop` disables automatic tips without removing wallet access.
- `@zectipbot start` permits replies again; it does not authorize spending.

The sender sets the amount, fee ceiling and daily limit after signing in. Prose, public amounts and quoted commands cannot authorize a tip. The command must be fresh and newer than the saved settings. Recipients must already have a ready wallet.

## Deployment switches

```text
PRIVATE_TIPPING_ENABLED=true
ZCASH_NETWORK=testnet
BOT_MODE=serve
X_POSTING_ENABLED=false
WALLET_ACCOUNTS_ENABLED=false
HOSTED_WALLETS_ENABLED=false
```

These are safe initial values. Account provisioning additionally requires `WALLET_ACCOUNTS_ENABLED=true` and the fixed broker socket `/run/zec-tip-bot-wallet-broker/wallet.sock`. The web container receives only that socket, not native RPC credentials, spending keys, recovery material or the native wallet database.

Wallet processing requires `HOSTED_WALLETS_ENABLED=true` **and** root-controlled Testnet transfer availability, synchronized compatible chain services, verified seed/mapping backup, and user authorization. No single frontend checkbox overrides these gates.

Automatic X processing requires `BOT_MODE=start` and `X_POSTING_ENABLED=true`; every public write verifies @zectipbot. Starting the website alone does not poll mentions. User sign-in consumes X API credits but never posts.

The local daily X testing guard defaults to enabled. The operator can set `X_USAGE_GUARD_ENABLED=false` to disable only the app's daily X cost/reply ceilings. X account billing/rate controls and wallet spending limits still apply; usage remains recorded.

Register the exact HTTPS `PUBLIC_BASE_URL/account/callback` in the existing X app, alongside the separate bot callback. Keep all API credentials in the ignored `.env`. Mainnet hosted transfers remain blocked.

## Withdrawals

Manual withdrawals work independently of the automatic-tip opt-in. They require a fresh X sign-in, owner-scoped form nonce, same-origin CSRF checks, a shielded-only Testnet Unified Address, review of the complete destination/amount/fee, and explicit final confirmation. An uncertain or pending payment must be checked before retrying.

The private wallet worker handles persisted authorized jobs without contacting X. Root operator controls can hold transfers. Account funding and confirmed balance reads remain separately gated.

## Recovery and pilot scope

The encrypted off-server snapshot includes native recovery material, the application account mappings and broker account mappings. Seed recovery and mapping consistency have been verified. A full native restore rehearsal remains outstanding.

The current backed-up account set contains two pilot accounts. Adding accounts changes that set and requires a fresh matching backup before funding/spending availability can be trusted. After restoring an old snapshot, hold workers, disable automatic profiles, and establish a new X command window before resuming. Never restore old state directly into active automatic spending.
