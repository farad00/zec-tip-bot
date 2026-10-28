# Native Testnet wallet backend

## Verified integration status — 28 October 2026

The deployed service has two X-linked native Testnet accounts, a verified encrypted off-server snapshot with matching account mappings, private confirmed balance reads, and a real spendable deposit. A native withdrawal was signed, broadcast, confirmed with at least five chain confirmations, and observed in the independent receiving wallet. The @zectipbot identity has been verified and automatic X processing has started; the first comment-triggered tip is still being tested.

The complete native restore rehearsal remains outstanding. Hosted Mainnet transfers are blocked. The operator knows user mappings and controls hosted funds; the product makes no anonymity-from-operator or self-custody claim.

## Pinned native builds

- Zallet source: `8c5a93ba9d68ad78d199c3624413c0139781b837`.
- Indexer-enabled Zebra source: `e8db87eb69254e0d4eedd058eadd48bdfa09065a`.
- NU7 Testnet branch: `77190ad9`, activation height `4465026`.

This is a source-pinned compatibility candidate, not an unmodified stable wallet release. Its released dependency cohort includes protocol 0.11, backend 0.25, SQLite 0.23 and Zebra state 16. The complete core suite passed 418 tests after the cleanup test's observation was corrected; wallet runtime code was unchanged by that test correction. A large native source build and full chain synchronization are required when reproducing this setup.

The wallet's Zebra backend reads a co-located node state database and private indexer service. The web application cannot replace these with an arbitrary public RPC endpoint. Wallet `external.broadcast=false`, backup confirmation and the native sync availability gate remain in place.

## Isolation

- Node RPC, indexer and wallet RPC bind to loopback.
- The web container sees only `/run/zec-tip-bot-wallet-broker/wallet.sock` through a read-only directory mount and client group.
- The broker has a fixed method allowlist and cannot export keys or act as a generic RPC proxy. The web container does not receive the native wallet database, RPC credentials or encryption identity.
- Native account labels and broker payment keys are opaque; X IDs are not used as native account names or transaction memos.

The native wallet's default database pool scales with detected CPU quota. Four long-lived synchronization connections exhausted the four-slot pool under a two-CPU quota, leaving status reads blocked. A four-CPU wallet allowance provides eight slots and restored status reads. Preserve sufficient pool capacity; bypassing the native sync gate is not a fix.

## Transaction path

`wallet-transfer-broker.py` creates proposals internally with exact decimal strings, 10 input confirmations and `FullPrivacy`. It accepts no imported PCZT. Inspection checks account hints, branch, recipient, amount, fee ceiling, expiry and absence of transparent inputs/outputs. The application receives an opaque proposal token.

The broker proves the proposal, rechecks terms, signs strictly, rechecks again, and extracts the final transaction. Native extraction verifies proofs/signatures and stores pending wallet state before broadcast. Native decoding independently checks the resulting ID, version, expiry and transparent input/output absence. The ID is not guessed by hashing the raw transaction with a generic Bitcoin hash.

Signed bytes are persisted before calling the local node's `sendrawtransaction`. A network-uncertain broadcast may query or rebroadcast those identical bytes; it never creates a replacement spend. Interrupted preparation or signing is held for review. Only a definitive insufficient-funds rejection before signing is treated as safely unsent.

The Testnet pilot caps individual amounts at 0.01 TAZ, fee ceilings at 0.001 TAZ, and total daily reservations at 0.1 TAZ. Users have their own fee-inclusive caps. App SQLite leases and broker atomic phase claims serialize concurrent processing.

## Recovery

The encrypted linked snapshot includes the native wallet database and encryption identity, application database/configuration, and broker database/configuration. The local verifier decrypts privately, checks database integrity and account mappings, and reproduces the native seed fingerprint. It never prints recovery words in its JSON report.

Off-server seed recovery and mapping consistency have passed. `nativeWalletRestoreVerified` remains false. A stale restored snapshot must not resume automatic spending: hold the worker, disable automatic profiles and establish a new command window before any replay-sensitive operation.

Funding/spending checks are tied to the backed-up account set. Adding a third account to the current two-account pilot requires a new matching snapshot and verification. This is a pilot restriction, not an unlimited public wallet rollout.
