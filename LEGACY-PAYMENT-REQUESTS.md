## Latest: private default-tip preview

Read PRIVATE-TIPPING.md first. X-user sign-in, private presets, comment resolution and durable authorization are implemented. Wallet provisioning, deposits, live signing and withdrawals are not connected. Do not fund this preview. Legacy request-link behavior below is disabled when PRIVATE_TIPPING_ENABLED=true.

# ZEC Tip Bot — hackathon MVP

**Confirmed product direction:** the hosted-wallet MVP will use tip to send the author's funds to a recipient. A future request would ask the tagged account to pay the author and is deferred. The current manual request implementation remains a legacy payment-link flow until that migration; it performs no automatic transfer.

**Deployment preparation:** [deploy/README.md](deploy/README.md) contains the persistent Linux setup and migration guide. Hosted Testnet wallets are the selected next phase; no signup, deposits or automatic spending are enabled yet.

**Latest:** [CARDS-UPGRADE.md](CARDS-UPGRADE.md) adds QR reply cards and renames the payment-request command to request. The tip command now returns no-send guidance; automatic wallet transfers are not enabled. See [WALLET-PLAN.md](WALLET-PLAN.md) for the separate signup-wallet proposal.

**New:** automatic operation and the redesigned UI are described in [UPGRADE.md](UPGRADE.md). Start there when updating an existing installation.

A Node.js 24 / TypeScript bot for **@zectipbot**, using the existing X developer app owned by **@farad_0**. It creates **ZIP-321 payment requests** with wallet links and QR codes. The payer signs and sends in their own wallet. The bot holds no ZEC, has no spending keys, performs no transfers, and does not verify or announce payment completion.

## Start here: authorize the right X account

In the VS Code terminal opened in this folder:

```powershell
npm ci --ignore-scripts
npm run setup
npm run build
```

Open `.env` **locally in VS Code**. Enter only the existing developer app's **API key** and **API key secret** in `X_API_KEY` and `X_API_SECRET`. These are OAuth 1.0a consumer credentials, not the OAuth 2.0 client secret or an app-only bearer token. Do not send them in chat, screenshots, issues, or a Git commit. Leave the access-token fields blank.

In the existing app's user authentication settings in [X Developer Console](https://console.x.com/), enable **OAuth 1.0a**, choose **Read and Write**, and register this callback exactly:

```text
http://127.0.0.1:3000/callback
```

Complete any required app website and description fields using real project information. The local callback must match `X_CALLBACK_URL`. If you changed permissions, authorize again; old tokens do not gain the new permissions automatically.

```powershell
npm run auth
```

Open **http://127.0.0.1:3000** in your own browser. Use the button and sign in to **@zectipbot** before approving the app. The account that owns the developer app and the account that authorizes the app can differ. The bot rejects any account other than @zectipbot and pins its numeric account ID. Only after the identity check succeeds are the new access token and access secret written into `.env`. Posting remains disabled.

```powershell
npm run verify
```

Expect `Verified @zectipbot` with its pinned account ID. This command reads identity and never posts. If you accidentally authorize @farad_0, the app refuses to save those tokens; restart authorization with the bot account. Do not paste any existing @farad_0 access tokens into the bot's user-token fields.

`.env`, `.env.*`, local data, dependencies, and build files are ignored by Git. `.env.example` contains only blank placeholders and defaults. Your filesystem, backups, and OneDrive can still copy local files: Git ignore is not encryption. Keep this folder private. The program never prints credentials, request headers, OAuth callback URLs, or upstream error bodies. Authorization request-token secrets stay in memory and expire after ten minutes. The program supports a loopback callback flow; no PIN workflow is needed.

## Your existing $5 and tokens

This project does **not** inspect your developer balance. Check the billing/project view of the existing app to confirm where those credits are allocated. Authorizing @zectipbot uses the same developer app; it does not transfer the credits into the bot's X account. Existing user access tokens for @farad_0 represent that account, so they are deliberately replaced with @zectipbot user authorization. Keep the app's API key and secret unless X requires regeneration.

## Demo immediately — no X account or credits needed

```powershell
npm run build
npm run demo
```

Open the local URL printed by the command. It shows a real QR code and ZIP-321 wallet link for a **public TESTNET sample address**. Never fund that sample address. No X request or post occurs. Stop the server with Ctrl+C.

## Live MVP walkthrough

1. Finish authorization and verification above. Leave the mainnet default for real recipient wallet addresses, or set `ZCASH_NETWORK=testnet` for a no-value demonstration. All registered addresses must match the selected network. Avoid changing networks while processing a backlog.
2. In another terminal, run `npm run serve`. Payment pages listen locally at port 3001. Make this server reachable through your chosen HTTPS hosting or tunnel. Set `PUBLIC_BASE_URL` to that **public HTTPS origin**, without a path, and restart bot commands. With a local tunnel, the default `WEB_HOST=127.0.0.1` is appropriate; on a host/container, set `WEB_HOST=0.0.0.0` and configure your host's TLS reverse proxy. No tunneling or hosting is purchased or started automatically. Keep the same SQLite data and server available for the lifetime of shared links.
3. Run `npm run bot:init` **once, before the demo commands are posted**. It verifies identity and sets a cursor starting now, skipping historical mentions.
4. From the recipient's X account, post exactly `@zectipbot register YOUR_RECEIVING_ADDRESS public`. Replace the address placeholder locally. The `public` keyword acknowledges that the receiving address is publicly associated with that X account. Never use a seed, private key, viewing key, or exchange account number.
5. Run `npm run bot:preview`. It fetches mentions and prints draft replies without updating registrations, creating payment requests, advancing the cursor, or posting. Preview still makes paid X reads. Registration is only applied during a live run.
6. When ready to publish those replies, set `X_POSTING_ENABLED=true` in `.env` and run `npm run bot:live`. The command verifies @zectipbot again immediately before every post. Only explicit supported commands get replies.
7. From the payer's X account, post `@zectipbot request @RECIPIENT 0.01 ZEC`. Replace `RECIPIENT` with the registered username. Preview and then run live. The bot resolves the current username to an X account ID and uses only that account's own registered address.
8. Open the reply's payment page, verify the **entire** address and network with the recipient, scan the QR or open the wallet link, and approve any payment in the wallet. A request is not proof of a payment. The URI can be paid repeatedly; check wallet history before sending again.

Manual preview/live commands run one bounded batch. Use `npm run bot:watch` for automatic checking every 60 seconds, with the same spending and reply limits. See UPGRADE.md for startup and shutdown details. At most three replies are attempted per run by default; remaining commands are left for the next run. Keep a short public hostname so reply text fits the conservative 280 ASCII-character limit.

### Supported commands

| X post text | Behavior |
| --- | --- |
| `@zectipbot help` | Replies with command and cost information. |
| `@zectipbot register ADDRESS public` | Validates and registers the author's own receiving address. |
| `@zectipbot request @user 0.01 ZEC` | Creates a request for the recipient's registered address. No funds move. |
| `@zectipbot stop` | Immediately opts the author out of new replies and deletes their current registration. Sends no confirmation reply. |
| `@zectipbot start` | Explicitly enables replies again; a deleted address must be registered again. |

Commands must be the entire post, beginning with the bot mention. The bot also checks X mention entities against the pinned bot ID. Unknown text, prose, quoted commands, supplied destination overrides, are ignored. Recognized commands with malformed arguments receive a concise format explanation. Replies mention no additional users; the request recipient is plain text in the reply. Opted-out recipients cannot receive new requests. Registering again updates the address for **future** requests only. Previously published X posts, immutable payment requests, copied URIs, and on-chain transactions cannot be revoked by opt-out. A sender can always pay an already-known address without this service.

## Costs and operating limits

**Bot service fee: 0 ZEC.** Sending a payment still costs the wallet's network fee, calculated and approved by the wallet in addition to the requested amount. The bot does not estimate network fees, deduct commissions, convert USD, or promise a fiat value.

X API credits are a separate **operator** cost. According to [X's pricing page](https://docs.x.com/x-api/getting-started/pricing), checked 27 October 2026:

| Operation | Published general rate | Local reservation |
| --- | --- | --- |
| User identity / recipient lookup | $0.010 per user returned | $0.010 per request |
| Mentions read | $0.005 per post returned | $0.050 per page of up to 10 |
| Create a reply containing a URL | $0.200 per request | $0.200 for **every** reply, even text-only |

The console's endpoint rates and actual charges take precedence. Owned-read and summoned-post discounts may apply, but this MVP does not assume eligibility. OAuth endpoint charges, retries you initiate, other applications, hosting, or price changes are not comprehensively covered by the local estimate. There is **no verified $5 balance** in this project.

A batch with one mention page, one recipient lookup, and one reply reserves up to **$0.280**: startup identity $0.010 + page $0.050 + lookup $0.010 + pre-post identity $0.010 + reply $0.200. A separate preview repeats the reads. Registration/help replies need no recipient lookup. Valid successful mention reads now settle to the actual returned resource count, including zero for empty pages. Failed or unreadable requests retain their conservative reservations. Historical estimates are preserved.

The defaults cap estimates at **$1 per UTC day**, three replies per run, ten reply attempts per UTC day, three mention pages per run, thirty API calls per batch, and a maximum tip request of **1 ZEC**. These are local safety limits, **not an X billing cap**. Set a project spending limit in X Console as well; leave auto-recharge off for a finite demo budget. `npm run status` shows local usage in thousandths of a dollar (`mills`), registered-user count, and unresolved replies without dumping addresses or credentials. Preserve `data/` across restarts; deleting it resets these local protections.

## Failure handling and recovery

- Wrong account / changed numeric ID: no post is sent. Authorize the dedicated bot account again. A previously pinned ID is also checked during reauthorization.
- 401/403: check app Read and Write permissions, user authorization, and endpoint access. 402: check credits. 429: wait for the X rate-limit reset. No automatic API retry occurs.
- Timeout or ambiguous write result: the post may have succeeded. A durable `sending`/`unknown` attempt prevents another automated reply to that interaction, even after restart. Inspect `npm run status` and the actual X account; reconcile manually. This favors avoiding duplicates over guaranteed delivery. No automatic resend command is provided.
- Crashed process: a one-hour SQLite lease prevents concurrent/restarted runs from racing. Wait for the lease to expire, confirm the prior process is stopped, then rerun. Payment pages can run in a separate process while the bot uses the same database.
- Pagination backlog larger than the configured limit: the bot stops before processing or advancing the cursor. Raise `MAX_MENTION_PAGES` cautiously after checking credit limits. Each successful live interaction advances the cursor; a per-run reply limit leaves later posts for the next run.
- Preview does not simulate registration changes within the same batch. Process registration live before previewing a dependent tip command.
- Payment page 410: the link expired (24 hours by default). Expiry cannot revoke an already-copied payment URI. Expired rows are kept locally; there is no retention cleanup job in this MVP.
- Keep the payment server's working directory at this folder; the server and bot must use the **same** `data/bot.sqlite`. HTTPS reachability and wallet behavior need a real-device check before sharing links.

## Validation and limits

```powershell
npm test
npm run check
```

The suite runs offline with mocked X responses plus a real loopback HTTP server. It covers OAuth signatures and callback matching, wrong-account refusal, immediate pre-post identity checks, token-file updates, ZIP-321 amount/memo encoding, address checksums/networks, an official Unified Address vector, opt-out, account-ID resolution, duplicate suppression, spending caps, preview isolation, pagination, HTML escaping, QR codes, and expired requests. Tests make no real X posts or payments.

Supported addresses: transparent Base58Check, Sapling Bech32, and **standard revision 0 Unified Addresses** (u/utest) with Orchard and/or Sapling receivers. UA decoding checks Bech32m, F4Jumble padding, receiver order and lengths, duplicates, and the shielded-receiver requirement. Experimental/unknown receiver types, metadata, draft revision 2 addresses, Sprout, viewing keys, and spending keys are intentionally rejected. This is encoding validation, not proof of ownership or a complete curve-point/receiver spendability check. The sending wallet must perform its own validation. Recipient registration proves control of the X account, not ownership of the address.

Only single-recipient, positive ZEC payment URI generation is implemented; there is no arbitrary URI parser, multi-pay, custom assets, balance ledger, fiat pricing, custody, withdrawal, blockchain scanning, or payment-confirmation service. No memo is included in public tip requests by default; the URI utility supports shielded UTF-8 memos up to 512 bytes. Public posts and payment pages expose the address, requested amount, username association, and timing regardless of shielded on-chain privacy. Payment IDs are random, unguessable 128-bit values but are public bearer links once shared. The page uses no analytics or third-party scripts; its first-party script handles copy controls, themes, expiry display, and the X composer; hosting infrastructure may still log requests.

This MVP uses fixed response templates and responds only to explicit bot commands, one reply per interaction, with `stop` opt-out. Review [X's automation rules](https://help.x.com/en/rules-and-policies/x-automation) before deployment. Do not add AI-generated replies without checking X's separate approval requirements.

## Protocol references

- [X OAuth 1.0a three-legged authorization](https://docs.x.com/fundamentals/authentication/oauth-1-0a/obtaining-user-access-tokens)
- [X OAuth signature construction](https://docs.x.com/fundamentals/authentication/oauth-1-0a/creating-a-signature)
- [X authenticated user](https://docs.x.com/x-api/users/get-my-user), [mentions](https://docs.x.com/x-api/users/get-mentions), [replies / create post](https://docs.x.com/x-api/posts/create-post)
- [ZIP-321 payment request URIs](https://zips.z.cash/zip-0321)
- [ZIP-316 Unified Addresses](https://zips.z.cash/zip-0316)
- [Official Zcash address test vectors](https://github.com/zcash/zcash-test-vectors/blob/master/test-vectors/json/unified_address.json)

## Hackathon checklist

- Today: run tests and offline demo; configure the existing app; authorize @zectipbot locally; verify identity; expose the payment server through HTTPS.
- Rehearse: initialize the cursor, register a consenting recipient, preview one tip, publish one reply, open the page and wallet on a real device. If using real ZEC, use a small amount you choose and confirm the full address yourself.
- Tomorrow, **28 October 2026 (Africa/Lagos)**: demonstrate request → X reply → QR/wallet approval. Describe the bot as a noncustodial payment-request bot. Confirmations are outside this MVP.

