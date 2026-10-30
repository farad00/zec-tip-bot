# ZEC Tip Bot

Shielded Testnet tipping from X comments. Set your amount privately, then reply under a post:

```text
@zectipbot tip
```

The author of the post you replied to receives your saved tip. You can also use `@zectipbot tip @recipient`. Both people need linked wallets. Public commands with amounts are rejected.

[Open the app](https://zectip.app/) · [Tests](https://github.com/farad00/zec-tip-bot/actions)

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

Subsequent testing created a third X-linked wallet through external signup, delivered its welcome grant, verified its encrypted account snapshot off-server, and confirmed a comment-triggered tip from that new account.

The `submission-2026-10-28` tag preserves the submitted two-account pilot. Subsequent work on `main` adds bounded external Testnet enrollment and welcome funding; it is separate from the submitted version.

## External testing

New users can sign in with X and create a wallet when the account service and encrypted recovery admission are ready. There is no fixed total-wallet cap. The first 12 new wallets are eligible for a 0.003 TAZ welcome grant. Funding needs chain confirmations before it is spendable. There is no purchase or Mainnet deposit involved.

Set the default tip to 0.001 TAZ, daily limit to 0.01 TAZ and maximum network fee to 0.001 TAZ. Enable automatic tips and save. Reply `@zectipbot tip` to a post by another linked account, or use `@zectipbot tip @farad_0` to try the existing recipient account. Check your private Activity for the result. A recipient without a wallet is not paid, and joining later never replays an earlier failed command.

The welcome budget is separate from user spending authorization: at most 12 grants and 0.048 TAZ in total, including fee ceilings. Each new binding requires an encrypted snapshot before admission. New snapshots initially remain on the VPS and must be exported, copied off-server and verified by the operator. Signup does not enable the user's automatic spending.

Available funds and funds still confirming are shown separately. The operator can select the three-confirmation Testnet pilot policy with `operator/update-confirming-balance.py`; the code defaults to ten. This policy applies to grants, incoming receipts, balances and new transaction input selection. Three confirmations provide less settlement assurance than ten. It does not enable Mainnet or change fee and spending caps.

## Development

Requires Node.js 24 or newer.

```sh
npm ci --ignore-scripts
npm run setup
npm run build
npm test
```

Store X credentials in the ignored `.env` file. `npm run auth` verifies @zectipbot before saving its bot token. User sign-in uses separate OAuth 2.0 Web App credentials and S256 PKCE. It requests only `users.read tweet.read`, the scopes X documents for `/2/users/me`; X still permits post reading under that grant, although signup only reads the numeric ID and username. It requests no write, DM, email or offline scopes. User tokens stay in memory and are revoked on a best-effort basis after the identity check; failed revocation leaves a short-lived token that is not retained. The bot continues using its own OAuth 1.0a authorization. The example configuration starts with posting, wallet provisioning and transfers disabled.

The Node/TypeScript app stores sessions and durable jobs in SQLite. It connects to a restricted local Python broker over a Unix socket. Zallet holds keys; a local Zebra node verifies and broadcasts transactions. Native wallet files and RPC credentials are not mounted into the web container.

See [deployment](deploy/README.md) for configuration and [wallet backend](deploy/WALLET-BACKEND.md) for source pins, transaction checks and recovery. Tests cover authorization, account isolation, command parsing, spending limits, duplicate handling, interrupted signing, broadcast recovery and private activity.

`X_USAGE_GUARD_ENABLED=false` disables local daily X cost/reply ceilings. X still charges API credits and enforces provider limits; wallet caps are unaffected. The default is `true`.

MIT licensed. Do not publish credentials, wallet databases, recovery archives or financial screenshots.

Enable OAuth 2.0 alongside OAuth 1.0a in the existing developer app, choose the confidential Web App type, and retain the exact public account callback. Set `X_USER_CLIENT_ID` and `X_USER_CLIENT_SECRET` privately in the ignored environment file. Missing signup credentials disable new sign-ins without falling back to the bot's grant. Existing wallet mappings and spending settings remain attached to the verified numeric X ID. Older OAuth 1.0a app grants are not automatically reduced; users can revoke those in X Settings → Security and account access → Apps and sessions → Connected apps, then sign in again. Do not revoke @zectipbot's authorization.
