# Native Testnet wallet backend

## Verified integration status — 28 October 2026

The deployed service has two X-linked native Testnet accounts, a verified encrypted off-server snapshot with matching account mappings, private confirmed balance reads, and a real spendable deposit. A native withdrawal was signed, broadcast, confirmed, and observed in the independent receiving wallet. The @zectipbot identity has been verified. A fresh X reply triggered an automatic native Testnet tip that reached 24 confirmations. The recipient saw the incoming balance and withdrew part of it to the independent Zingo Testnet wallet; the user confirmed receipt and the withdrawal reached 6 confirmations.

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

The read-only receipt endpoint uses `z_listtransactions` scoped to the bound native account, limited to 30 recent rows. It rejects mixed-account results, excludes change and already-known bot transfers, and returns only an opaque receipt key, amount, confirmation count and block time. Native addresses, memos and raw transaction IDs are stripped before the response reaches the website. Receipts do not affect balance calculations, spending authorizations or signing state.

Signed bytes are persisted before calling the local node's `sendrawtransaction`. A network-uncertain broadcast may query or rebroadcast those identical bytes; it never creates a replacement spend. Interrupted preparation or signing is held for review. A definitive insufficient-funds rejection or validated fee-only refusal before signing is treated as safely unsent. An operator reconciliation of the initial unsigned fee refusal checked persisted evidence and retained the rejected request key; it did not replay the command.

The Testnet pilot caps individual amounts at 0.01 TAZ, fee ceilings at 0.001 TAZ, and total daily reservations at 0.1 TAZ. Users have their own fee-inclusive caps. App SQLite leases and broker atomic phase claims serialize concurrent processing.

## Recovery

The encrypted linked snapshot includes the native wallet database and encryption identity, application database/configuration, and broker database/configuration. The local verifier decrypts privately, checks database integrity and account mappings, and reproduces the native seed fingerprint. It never prints recovery words in its JSON report.

Off-server seed recovery and mapping consistency have passed. `nativeWalletRestoreVerified` remains false. A stale restored snapshot must not resume automatic spending: hold the worker, disable automatic profiles and establish a new command window before any replay-sensitive operation.

The submitted version tied funding/spending checks to the backed-up two-account set. The later `wallet-enrollment.py` adapter keeps those baseline accounts available while admitting new bindings only after a durable encrypted snapshot. An active snapshot's loss or corruption holds availability; a failed new snapshot leaves that new account unadmitted. Snapshotting does not export the native encryption identity. New snapshots require off-server copying and private verification; their existence on the VPS is not described as off-server recovery.

The optional welcome worker has a separate operator-approved grant budget. It pays only newly admitted bindings, with a single permanent request key per numeric X ID. It uses the same validated FullPrivacy transaction path, native reservations, signature claims and identical-byte broadcast recovery as other transfers. Signup never enables a user's own automatic spending. The broker remains Testnet-only and caps native accounts at 16.
