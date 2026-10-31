# ZEC Tip Bot

Shielded Testnet tipping from X comments. Set your amount privately, then reply under a post:

```text
@zectipbot tip
```

The author of the post you replied to receives your saved tip. You can also use `@zectipbot tip @recipient`. Both people need linked wallets. Public commands with amounts are rejected.

[Open the app](https://zectip.app/) · [Tests](https://github.com/farad00/zec-tip-bot/actions)

## How to test (judges)

**Live demo: [zectip.app/account](https://zectip.app/account)** — no local installation needed. This is hosted **Zcash Public Testnet** tipping; TAZ has no monetary value. Use your own X account. No shared test-account password or developer API key is needed.

1. **Sign in and create a wallet.** Continue with X, then choose **Create my Testnet wallet**. The wallet is linked to your verified X ID. Signing in does not enable automatic spending.
2. **Wait for available funds.** The first **12 new wallets** are eligible for a **0.003 TAZ** welcome grant, subject to the remaining grant budget and wallet readiness. Wait until it appears in **Available balance**, rather than confirming funds. If no grant is available, use the funding steps below.
3. **Save a tip preset.** Set **Default tip: 0.001 TAZ**, **Daily limit: 0.01 TAZ**, and **Maximum network fee: 0.001 TAZ**. Tick **Automatic tips** and the consent, then **Save settings**. Public acknowledgement is optional. Your available funds must cover the tip and actual network fee.
4. **Post one command from that same X account.** Reply directly under a post by the existing linked recipient [@farad_0](https://x.com/farad_0) with `@zectipbot tip`. Alternatively, post `@zectipbot tip @farad_0`. Do not add an amount or address. You do not need a second account to test sending.
5. **Check your private Activity.** Expect **Confirming → Confirmed** after the command is processed and the transaction receives confirmations. The page updates every 15 seconds while visible; command polling and block confirmation also take time. A public acknowledgement only confirms command receipt, not payment. Do not repeat a pending command.
6. **Optional withdrawal.** To check an independent wallet, withdraw a small amount such as **0.0005 TAZ**, if your remaining available funds cover it and the actual fee, to your own compatible shielded-only **Testnet** address. Use a **0.001 TAZ** maximum fee, review the full destination and amount, and confirm once. Check receipt in the separate wallet.

**If a command is not sent:** Activity explains missing setup, insufficient funds, fee or daily limits, or an unavailable recipient wallet. A recipient must create a wallet before a fresh command can pay them; signup never replays a failed command. `stop` disables automatic tips and replies. `start` resumes replies only; enable spending again in private settings.

Commands are deduplicated, edited tip commands are rejected, and uncertain signing results require review rather than a new payment attempt.

### Testnet funding if the welcome grant is unavailable

1. Use a separate wallet connected to **Zcash Public Testnet**, not Mainnet or a private staging network.
2. If it needs funds, check the [Public Testnet faucet](https://faucet.testnet.valargroup.dev/) and [network/faucet status](https://zakura.com/nu7/). Request using that wallet's supported Testnet receiving address. Faucet availability and limits are controlled by its operator; a link does not guarantee a payout. Check any claim status before submitting again. If unavailable, use an already funded Public Testnet wallet.
3. In the bot wallet, open **Receive** and privately copy your Testnet deposit address. Send a small shielded Testnet payment to it from the separate wallet.
4. Wait for the displayed confirmation requirement and **Available balance**, then continue from step 3 above. Never send Mainnet ZEC to this demo or enter recovery words into a faucet.

For local automated tests and deployment details, see [Development](#development) and the [deployment guide](deploy/README.md).

## Privacy and fees

Amounts, addresses and payment outcomes stay in signed-in account pages. Optional public acknowledgements only confirm command receipt and link to private status. Exact tip commands from senders without enabled presets receive setup guidance, including first-time users; explicit stop suppresses it. Setup guidance never distinguishes an unregistered sender from one with incomplete or disabled settings. Recipient wallet availability and payment outcomes remain private. Public X comments still reveal tipping intent and interactions. The operator knows X-to-wallet mappings and controls hosted funds; this is a hosted wallet.

Transactions use `FullPrivacy` and reject transparent inputs and outputs. The bot service fee is zero. Network fees apply. A fee above the saved maximum is refused before signing. Daily reservations include fees.

This is **Testnet only**. Testnet coins have no monetary value. Hosted Mainnet transfers are disabled. Individual payments are limited to 0.01 TAZ, fees to 0.001 TAZ, and broker-wide daily reservations to 0.1 TAZ.

## Verified flow

The submitted build completed external funding, an automatic X-comment tip, credit to the second account, and a withdrawal received in an independent Zingo Testnet wallet. Both the tip and withdrawal were confirmed on chain. Native seed recovery and the two-account backup were verified off-server; a complete native restore rehearsal remains outstanding.

Subsequent testing created a third X-linked wallet through external signup, delivered its welcome grant, verified its encrypted account snapshot off-server, and confirmed a comment-triggered tip from that new account.

The `submission-2026-10-28` tag preserves the submitted two-account pilot. Subsequent work on `main` adds bounded external Testnet enrollment and welcome funding; it is separate from the submitted version.

## Enrollment and funding limits

New users can sign in with X and create a wallet when the account service and encrypted recovery admission are ready. There is no fixed total-wallet cap. The first 12 new wallets are eligible for a 0.003 TAZ welcome grant. Funding needs chain confirmations before it is spendable. There is no purchase or Mainnet deposit involved.

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

## Wallet updates and receipts

Visible wallet pages refresh their owner-only balance and Activity snapshot every 15 seconds. Polling pauses in background tabs, backs off on failures, and never submits or replaces a form. Session expiry or a different sign-in clears the old private view. Financial data is not saved in browser storage. Cached v3 scripts remain unchanged; current pages load v4.

A saved preset is not a promise that funds will be available later. If the native wallet rejects a payment for insufficient spendable funds, the command fails before signing and Activity explains the reason. The saved amount is not reduced, and the failed command is not retried after funding. A balance warning also flags presets that exceed the current available balance. Public acknowledgements stay generic.

Incoming shielded withdrawals from another hosted account appear as private deposit receipts. Receipt deduplication only removes a tip already represented in the signed-in recipient's Activity; it does not hide all transactions created by the broker. Transparent outputs, change and nonpositive account balance changes remain excluded.
