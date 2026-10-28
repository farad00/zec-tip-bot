import { createServer, type Server } from 'node:http';
import QRCode from 'qrcode';
import type { Config } from './config.js';
import { State, type Payment } from './state.js';
import { paymentUri } from './zcash.js';
import { CSS, CLIENT_JS } from './ui.js';
import { accountRoutes } from './account.js';
import { paymentCard, paymentPageUrl, cardAlt } from './cards.js';

export const escapeHtml = (v: string): string => v.replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch]!);
const e = escapeHtml;
function icon(name: string) {
  const paths: Record<string, string> = {
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/>',
    shield: '<path d="M12 3l8 3v6c0 5-8 9-8 9S4 17 4 12V6z"/><path d="M8 12l3 3 5-6"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2"/>',
    chevron: '<path d="M9 5l7 7-7 7"/>',
    wallet: '<path d="M20 8H5a2 2 0 0 1 0-4h13v4M4 6v14h17V8"/><path d="M21 12h-6v4h6"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
    x: '<path d="M5 4l14 16M19 4L5 20"/>'
  };
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (paths[name] ?? paths.arrow) + '</svg>';
}
function layout(body: string, title = 'ZEC Tip Bot — a little ZEC goes a long way', shareHead = '') {
  return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><meta name="description" content="Create a Zcash payment request from an X mention. Review and pay in your own wallet.">' + shareHead + '<title>' + e(title) + '</title><link rel="icon" href="/favicon.svg" type="image/svg+xml"><style>' + CSS + '</style><script src="/assets/app.js" defer></script></head><body><main class="shell">' +
    '<header class="nav"><a class="brand" href="/" aria-label="ZEC Tip Bot home"><span class="mark" aria-hidden="true">Z</span>ZEC <span>TIP BOT</span></a><nav class="nav-actions" aria-label="Main navigation"><a class="quiet-link" href="/#commands">How it works</a><a class="quiet-link" href="https://x.com/zectipbot" target="_blank" rel="noopener noreferrer">Meet the bot ' + icon('arrow') + '</a><button type="button" class="icon-button" data-theme-toggle aria-label="Toggle light and dark theme">' + icon('sun') + '</button></nav></header>' +
    body + '<footer class="footer"><span>Small gesture. Full control.<br>Built for Zcash.</span><div class="footer-links"><a href="/#commands">Command guide</a><a href="https://zips.z.cash/zip-0321" target="_blank" rel="noopener noreferrer">ZIP-321</a><span>No custody. No payment tracking.</span></div></footer></main><div id="toast" class="toast" role="status" aria-live="polite"></div></body></html>';
}
function flow() {
  return '<section class="flow" aria-label="How a request works"><div class="flow-item"><span class="step">01 /</span><div><strong>A mention starts it.</strong><p>The bot creates a request for a registered recipient.</p></div></div><div class="flow-item"><span class="step">02 /</span><div><strong>Your wallet takes over.</strong><p>Review the full address, amount, and network fee.</p></div></div><div class="flow-item"><span class="step">03 /</span><div><strong>You make the call.</strong><p>Only your wallet can approve and send the payment.</p></div></div></section>';
}
function home(c: Config) {
  const cards = [
    ['01 / REGISTER', 'Make yourself receivable.', 'Post your own receiving address. The word public acknowledges that it will be associated with your X account.', '@zectipbot register YOUR_ADDRESS public'],
    ['02 / REQUEST', 'Turn appreciation into a request.', 'Name the recipient and amount in ZEC. They must register first. The payer completes the payment in their wallet.', '@zectipbot request @recipient 0.001 ZEC'],
    ['03 / MANAGE', 'Your address. Your choice.', 'Unregister removes your receiving address. Stop removes it and disables replies. Old shared links remain public.', '@zectipbot unregister'],
    ['04 / HELP', 'A little help, on X.', 'Send help for examples. Use start to enable replies again after stop, then register your address again.', '@zectipbot help']
  ].map(([tag, title, copy, command]) => '<article class="command-card"><span class="eyebrow muted">' + e(tag!) + '</span><h3>' + e(title!) + '</h3><p>' + e(copy!) + '</p><code>' + e(command!) + '</code></article>').join('');
  return layout('<section class="hero"><div><span class="pill">' + icon('x') + ' AN X MENTION. A ZCASH REQUEST.</span><h1>A little ZEC.<br>A lot of <em>appreciation.</em></h1><p class="hero-copy">Give a good post a little more than a like. Create a payment request on X, then let your own wallet do the sending.</p><div class="hero-foot"><span>' + icon('shield') + ' Your keys stay yours</span><span>0 ZEC bot fee</span></div></div>' +
    '<form id="composer" class="composer" data-max="' + e(c.maxTip) + '"><span class="eyebrow muted">MAKE A PAYMENT REQUEST</span><h2>Who made your day?</h2><p>Start with their X username. They need a registered receiving address.</p><label class="field" for="recipient">Recipient on X</label><div class="input-wrap"><span class="prefix">@</span><input id="recipient" name="recipient" placeholder="recipient" maxlength="16" autocomplete="off" spellcheck="false" aria-describedby="composer-error"></div><label class="field" for="tip-amount">Amount</label><div class="input-wrap"><input id="tip-amount" name="amount" value="0.001" inputmode="decimal" autocomplete="off" aria-describedby="composer-error"><span>ZEC</span></div><p id="composer-error" class="field-error" role="status"></p><div id="command-output" class="command-preview">@zectipbot request @recipient 0.001 ZEC</div><a id="x-intent" class="button" aria-disabled="true" target="_blank" rel="noopener noreferrer">Open X composer ' + icon('arrow') + '</a><button type="button" id="copy-command" class="button secondary" data-copy-target="command-output" disabled>' + icon('copy') + ' Copy command</button><p class="hint">Creates a request. Your wallet sends the ZEC.</p><noscript><p class="hint">Use the command guide below to post a request on X.</p></noscript></form></section>' +
    '<section class="principles" aria-label="How we work"><div>' + icon('shield') + '<span>No deposits. You hold your keys.</span></div><div>' + icon('wallet') + '<span>Pay with a compatible Zcash wallet.</span></div><div>' + icon('info') + '<span>0 bot fee. Wallet network fees apply.</span></div></section>' +
    '<section class="guide" id="commands"><div class="section-head"><div><span class="eyebrow muted">SMALL COMMANDS. CLEAR INTENT.</span><h2>From a post to a payment request.</h2></div><p>One complete command per post. No guesswork about the recipient, currency, or amount.</p></div><div class="command-grid">' + cards + '</div><p class="end-note">Registration and payment links are public: they reveal the receiving address, X association, and requested amount. Shielded on-chain transfers do not hide information you publish on X. Never post a seed or private key.</p></section>' + flow());
}
function requestPage(p: Payment, uri: string, qr: string, base: string) {
  let shareHead = '', sharePanel = '';
  if (new URL(base).protocol === 'https:') {
    const url = paymentPageUrl(p, base), image = url + '/card.png';
    const title = p.amount + ' ZEC for @' + p.recipient;
    const description = (p.network === 'testnet' ? 'TESTNET. ' : '') + 'Payment request. Review and pay in your wallet. Payment unverified. Bot fee 0 ZEC; wallet network fees apply.';
    shareHead = '<meta name="twitter:card" content="summary_large_image"><meta name="twitter:site" content="@zectipbot">' +
      '<meta name="twitter:title" content="' + e(title) + '"><meta name="twitter:description" content="' + e(description) + '">' +
      '<meta name="twitter:image" content="' + e(image) + '"><meta name="twitter:image:alt" content="' + e(cardAlt(p)) + '">' +
      '<meta property="og:type" content="website"><meta property="og:url" content="' + e(url) + '"><meta property="og:title" content="' + e(title) + '">' +
      '<meta property="og:description" content="' + e(description) + '"><meta property="og:image" content="' + e(image) + '">' +
      '<meta property="og:image:width" content="1200"><meta property="og:image:height" content="675">';
    sharePanel = '<details class="details"><summary>Share a payment card</summary><p>A QR opens this request page. Use the reply link when paying on the same phone. This static image cannot show whether a payment was made.</p>' +
      '<img class="share-card" src="/p/' + e(p.id) + '/card.png" alt="' + e(cardAlt(p)) + '" width="1200" height="675" loading="lazy">' +
      '<a class="button secondary" href="/p/' + e(p.id) + '/card.png" download="zec-request-' + e(p.id.slice(0,8)) + '.png">Save payment card</a></details>';
  }
  const expiry = new Date(p.expires).toISOString();
  return layout('<div class="breadcrumb"><a href="/">Home</a>' + icon('chevron') + '<span>Payment request</span></div><section class="request-grid"><div><div class="request-intro"><span class="eyebrow muted">A LITTLE APPRECIATION FOR</span><span class="pill"><span class="dot"></span>' + e(p.network.toUpperCase()) + '</span></div><h1 class="amount">' + e(p.amount) + '<span class="currency">ZEC</span></h1><p class="amount-sub">A payment request for <strong>@' + e(p.recipient) + '</strong>.</p>' +
    '<div class="recipient"><span class="avatar" aria-hidden="true">' + e(p.recipient.slice(0, 2).toUpperCase()) + '</span><div><strong>@' + e(p.recipient) + '</strong><p>Receiving address registered by this X account</p></div></div><div class="section-label"><label for="receiving-address">Full receiving address</label><button type="button" class="small-button" data-copy-target="receiving-address">' + icon('copy') + ' Copy address</button></div><textarea id="receiving-address" class="address" readonly rows="4" spellcheck="false">' + e(p.address) + '</textarea><p class="address-note">Check the entire address with the recipient before approving a payment.</p>' +
    '<dl class="fees"><div><dt>Requested amount</dt><dd>' + e(p.amount) + ' ZEC</dd></div><div><dt>Bot service fee</dt><dd class="zero">0 ZEC</dd></div><div><dt>Network fee</dt><dd>Calculated by your wallet</dd></div><div class="total"><dt>You review in your wallet</dt><dd>' + e(p.amount) + ' ZEC + network fee</dd></div></dl>' +
    '<div class="details"><details><summary>What happens when I open my wallet?</summary><p>Your wallet reads the address and amount from this ZIP-321 request. It checks the destination and network, calculates fees, and asks you to approve. Opening this page sends no funds. No fiat conversion is performed here.</p></details><details><summary>Privacy and payment status</summary><p>This link is public and associates the address and amount with an X account. No funds have been sent or confirmed by this bot. The bot cannot check if this request has already been paid. A request can be paid more than once; check your wallet history before sending.</p></details><details><summary>Can this request be changed or cancelled?</summary><p>The address and amount are fixed when the request is created. Changing registration or opting out does not revoke this link or an already-copied URI. This web link expires ' + e(expiry) + ', but a copied address can still receive funds.</p></details></div><div class="request-meta"><span>REQUEST ' + e(p.id.slice(0, 8).toUpperCase()) + '</span><time datetime="' + e(expiry) + '" data-expiry="' + e(expiry) + '">Expires ' + e(expiry) + '</time></div></div>' +
    '<aside><div class="wallet-card"><span class="eyebrow muted">YOUR WALLET. YOUR CALL.</span><h2>Ready to review?</h2><p>Open this request on your device, or scan it with a mobile Zcash wallet.</p><a class="button" href="' + e(uri) + '" data-wallet-link>' + icon('wallet') + ' Open in your Zcash wallet ' + icon('arrow') + '</a><div class="or">OR SCAN WITH YOUR PHONE</div><div class="qr-shell"><img alt="Zcash payment request QR code" src="' + qr + '" width="216" height="216"></div><p class="qr-caption">Scan to load ' + e(p.amount) + ' ZEC in your wallet.</p><button type="button" class="small-button" data-copy-page>' + icon('copy') + ' Copy request link</button></div><div class="review-note">' + icon('info') + '<div><strong>Request created. Payment unverified.</strong>Only approve in your wallet after checking the destination and network fee.</div></div><details class="details"><summary>Wallet did not open?</summary><p>Copy this payment URI into a compatible wallet, or scan the QR code.</p><label class="section-label" for="payment-uri">ZIP-321 payment URI</label><textarea id="payment-uri" class="address" rows="6" readonly>' + e(uri) + '</textarea><button type="button" class="small-button" data-copy-target="payment-uri">' + icon('copy') + ' Copy payment URI</button></details>' + sharePanel + '</aside></section>' + flow(), p.amount + ' ZEC for @' + p.recipient + ' — ZEC Tip Bot', shareHead);
}
function errorPage(title: string, message: string) {
  return layout('<section class="error-page"><span class="eyebrow muted">ZEC TIP BOT</span><h1>' + e(title) + '</h1><p>' + e(message) + '</p><a class="button" href="/">Back to the command guide ' + icon('arrow') + '</a></section>', title + ' — ZEC Tip Bot');
}
export function paymentServer(c: Config, state: State): Server {
  const accounts = c.privateTips ? accountRoutes(c,state) : undefined;
  const cardCache = new Map<string, Buffer>(); // at most 16 small PNGs; expiry is checked before every response
  return createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store'); res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('X-Content-Type-Options', 'nosniff'); res.setHeader('X-Robots-Tag', 'noindex, nofollow');
    res.setHeader('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src data: 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'");
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    if (accounts) {
      if (await accounts(req,res)) return;
      if ((req.url ?? '').startsWith('/p/')) { res.writeHead(404).end('Public payment pages are disabled in private tipping mode.'); return; }
    }
    if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405).end(); return; }
    const path = (req.url ?? '').split('?')[0];
    if (path === '/healthz') { res.setHeader('Content-Type', 'application/json'); res.end('{"ok":true}'); return; }
    if (path === '/assets/app.js') { res.setHeader('Content-Type', 'text/javascript; charset=utf-8'); res.end(req.method === 'HEAD' ? '' : CLIENT_JS); return; }
    if (path === '/favicon.svg') { res.setHeader('Content-Type', 'image/svg+xml'); res.end('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><circle cx="20" cy="20" r="20" fill="#efbb43"/><text x="19" y="29" text-anchor="middle" font-family="Georgia,serif" font-size="29" font-weight="bold" fill="#372807">Z</text></svg>'); return; }
    if (path === '/') { res.end(req.method === 'HEAD' ? '' : home(c)); return; }
    const match = /^\/p\/([a-f0-9]{32})(\/card\.png)?$/.exec(path ?? '');
    const payment = match ? state.payment(match[1]!) : undefined;
    if (!payment) { res.writeHead(404).end(errorPage('This request is missing.', 'Check the link in the original bot reply, or create a fresh request on X.')); return; }
    if (payment.expires <= Date.now()) { res.writeHead(410).end(errorPage('This request has expired.', 'Create a fresh request on X. Expiry cannot revoke a payment URI or receiving address that has already been copied.')); return; }
    try {
      if (match?.[2]) {
        res.setHeader('Content-Type', 'image/png');
        res.setHeader('Content-Disposition', 'inline; filename="zec-request-' + payment.id.slice(0,8) + '.png"');
        if (req.method === 'HEAD') { res.end(); return; }
        let png = cardCache.get(payment.id);
        if (!png) {
          png = paymentCard(payment, c.baseUrl);
          if (cardCache.size >= 16) cardCache.delete(cardCache.keys().next().value!);
          cardCache.set(payment.id, png);
        }
        res.end(png); return;
      }
      const uri = paymentUri(payment);
      const qr = await QRCode.toDataURL(uri, { errorCorrectionLevel: 'M', margin: 2, width: 512 });
      if (req.method === 'HEAD') { res.end(); return; }
      res.end(requestPage(payment, uri, qr, c.baseUrl));
    } catch { res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.writeHead(400).end(errorPage('This request could not be opened.', 'Ask for a new request. No payment was sent by the bot.')); }
  });
}
export async function listen(server: Server, host: string, port: number) {
  await new Promise<void>((resolve, reject) => { server.once('error', () => reject(new Error('Could not start the payment server. Check WEB_HOST and WEB_PORT.'))); server.listen(port, host, resolve); });
}

