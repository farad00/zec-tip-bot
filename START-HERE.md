# Start here

The active product is **private comment tipping on Zcash Testnet**. Read [README.md](README.md) for the verified integration status, commands, privacy boundary and setup.

Local development starts with Node.js 24:

```sh
npm ci --ignore-scripts
npm run setup
npm run build
```

Credentials belong only in the ignored local `.env`. Use the existing developer app; authorize @zectipbot separately from personal user sign-in. The account owner sets a tip amount, fee ceiling and daily limit privately.

Native provisioning and spending require the isolated Testnet backend and verified recovery. Fresh configurations keep them disabled. A successful local website preview is not a funded wallet integration.

The old public registration and ZIP-321 request walkthrough is preserved as historical documentation in [LEGACY-PAYMENT-REQUESTS.md](LEGACY-PAYMENT-REQUESTS.md). It is disabled in the active private mode.
