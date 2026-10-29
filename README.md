# ZEC Tip Bot

Shielded Testnet tipping from X comments. Set your amount privately, then reply under a post:

```text
@zectipbot tip
```

The author of the post you replied to receives your saved tip. You can also use `@zectipbot tip @recipient`. Both people need linked wallets. Public commands with amounts are rejected.

[Open the app](https://178-18-247-12.sslip.io/) · [Tests](https://github.com/farad00/zec-tip-bot/actions)

## How it works

1. Sign in with X and create a hosted Testnet wallet linked to your verified account ID.
2. Fund the wallet. Set a default tip, daily spending cap and maximum network fee, then enable automatic tips.
3. Post one tip command. Track the payment in your private wallet.
4. Withdraw to a shielded Testnet wallet after reviewing the destination and amount.

`stop` disables automatic tipping. `start` resumes replies; spending must be enabled again in private settings. Commands are deduplicated, edited commands are rejected, and uncertain signing results require review rather than a new payment attempt.

## Privacy and fees

Amounts, addresses and payment outcomes stay in signed-in account pages. Optional public replies only acknowledge command receipt. Public X comments still reveal tipping intent and interactions. The operator knows X-to-wallet mappings and controls hosted funds; this is a hosted wallet.

Transactions use `FullPrivacy` and reject transparent inputs and outputs. The bot service fee is zero. Network fees apply. A fee above the saved maximum is refused before signing. Daily reservations include fees.

This is **Testnet only**. Testnet coins have no monetary value. Hosted Mainnet transfers are disabled. Individual payments are limited to 0.01 TAZ, fees to 0.001 TAZ, and broker-wide daily reservations to 0.1 TAZ.

## Verified flow

The submitted build completed external funding, an automatic X-comment tip, credit to the second account, and a withdrawal received in an independent Zingo Testnet wallet. Both the tip and withdrawal were confirmed on chain. Native seed recovery and the two-account backup were verified off-server; a complete native restore rehearsal remains outstanding.

The submitted pilot has two funded accounts, with additional wallet enrollment paused. The `submission-2026-10-28` tag preserves that build. Later development is separate from the submitted version.

## Development

Requires Node.js 24 or newer.

```sh
npm ci --ignore-scripts
npm run setup
npm run build
npm test
```

Store X credentials in the ignored `.env` file. `npm run auth` verifies @zectipbot before saving its bot token. User sign-in separately verifies each numeric X ID and discards the user's access credentials after sign-in. The example configuration starts with posting, wallet provisioning and transfers disabled.

The Node/TypeScript app stores sessions and durable jobs in SQLite. It connects to a restricted local Python broker over a Unix socket. Zallet holds keys; a local Zebra node verifies and broadcasts transactions. Native wallet files and RPC credentials are not mounted into the web container.

See [deployment](deploy/README.md) for configuration and [wallet backend](deploy/WALLET-BACKEND.md) for source pins, transaction checks and recovery. Tests cover authorization, account isolation, command parsing, spending limits, duplicate handling, interrupted signing, broadcast recovery and private activity.

`X_USAGE_GUARD_ENABLED=false` disables local daily X cost/reply ceilings. X still charges API credits and enforces provider limits; wallet caps are unaffected. The default is `true`.

MIT licensed. Do not publish credentials, wallet databases, recovery archives or financial screenshots.
