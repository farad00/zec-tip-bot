import { deflateSync, inflateSync } from 'node:zlib';
import QRCode from 'qrcode';
import { CARD_FONT } from './card-font.js';
import type { Payment } from './state.js';
import { paymentUri } from './zcash.js';

export const CARD_WIDTH = 1200, CARD_HEIGHT = 675;
type Color = readonly [number, number, number];
const PAPER: Color = [245, 244, 239], INK: Color = [28, 43, 34], MUTED: Color = [101, 114, 104];
const AMBER: Color = [239, 187, 67], LINE: Color = [217, 224, 213], WHITE: Color = [255, 255, 255];
const glyphs = new Map<string, Buffer>();
const crcTable = Array.from({ length: 256 }, (_, i) => {
  let v = i; for (let bit = 0; bit < 8; bit++) v = (v & 1) ? 0xedb88320 ^ (v >>> 1) : v >>> 1;
  return v >>> 0;
});
function chunk(type: string, bytes: Buffer) {
  const name = Buffer.from(type); const body = Buffer.concat([name, bytes]);
  let crc = 0xffffffff; for (const v of body) crc = crcTable[(crc ^ v) & 255]! ^ (crc >>> 8);
  const head = Buffer.alloc(4), tail = Buffer.alloc(4); head.writeUInt32BE(bytes.length); tail.writeUInt32BE((crc ^ 0xffffffff) >>> 0);
  return Buffer.concat([head, body, tail]);
}
class Canvas {
  readonly pixels = Buffer.alloc(CARD_WIDTH * CARD_HEIGHT * 3);
  constructor() { this.rect(0, 0, CARD_WIDTH, CARD_HEIGHT, PAPER); }
  pixel(x: number, y: number, color: Color, alpha = 1) {
    if (x < 0 || y < 0 || x >= CARD_WIDTH || y >= CARD_HEIGHT || alpha <= 0) return;
    const pos = (y * CARD_WIDTH + x) * 3;
    for (let i = 0; i < 3; i++) this.pixels[pos + i] = Math.round(this.pixels[pos + i]! * (1 - alpha) + color[i]! * alpha);
  }
  rect(x: number, y: number, w: number, h: number, color: Color, radius = 0) {
    for (let dy = 0; dy < h; dy++) for (let dx = 0; dx < w; dx++) {
      const cx = Math.max(radius - dx - .5, dx + .5 - (w - radius), 0);
      const cy = Math.max(radius - dy - .5, dy + .5 - (h - radius), 0);
      const alpha = radius && cx && cy ? Math.max(0, Math.min(1, radius + .5 - Math.hypot(cx, cy))) : 1;
      this.pixel(x + dx, y + dy, color, alpha);
    }
  }
  text(value: string, x: number, baseline: number, size: number, color: Color, maxWidth = 1100) {
    const advance = [...value].reduce((n, ch) => n + (CARD_FONT[ch]?.[2] ?? 0), 0);
    const scale = Math.min(size / 96, maxWidth / Math.max(1, advance));
    for (const ch of value) {
      const g = CARD_FONT[ch]; if (!g) continue;
      const [w, h, step, left, top, data] = g;
      if (!glyphs.has(ch)) glyphs.set(ch, inflateSync(Buffer.from(data, 'base64')));
      const mask = glyphs.get(ch)!;
      for (let py = 0; py < Math.ceil(h * scale); py++) for (let px = 0; px < Math.ceil(w * scale); px++) {
        const sx = Math.min(w - 1, (px + .5) / scale - .5), sy = Math.min(h - 1, (py + .5) / scale - .5);
        const ix = Math.max(0, Math.floor(sx)), iy = Math.max(0, Math.floor(sy));
        const tx = Math.max(0, sx - ix), ty = Math.max(0, sy - iy);
        const sample = (a: number, b: number) => mask[Math.min(h - 1, b) * w + Math.min(w - 1, a)]!;
        const alpha = ((sample(ix, iy) * (1 - tx) + sample(ix + 1, iy) * tx) * (1 - ty) +
          (sample(ix, iy + 1) * (1 - tx) + sample(ix + 1, iy + 1) * tx) * ty) / 255;
        this.pixel(Math.round(x + left * scale) + px, Math.round(baseline + top * scale) + py, color, alpha);
      }
      x += step * scale;
    }
    return x;
  }
  png() {
    const header = Buffer.alloc(13); header.writeUInt32BE(CARD_WIDTH); header.writeUInt32BE(CARD_HEIGHT, 4); header[8] = 8; header[9] = 2;
    const rows = Buffer.alloc((CARD_WIDTH * 3 + 1) * CARD_HEIGHT);
    for (let y = 0; y < CARD_HEIGHT; y++) this.pixels.copy(rows, y * (CARD_WIDTH * 3 + 1) + 1, y * CARD_WIDTH * 3, (y + 1) * CARD_WIDTH * 3);
    return Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]), chunk('IHDR', header), chunk('IDAT', deflateSync(rows)), chunk('IEND', Buffer.alloc(0))]);
  }
}
export function paymentPageUrl(p: Pick<Payment, 'id'>, base: string) {
  if (!/^[a-f0-9]{32}$/.test(p.id)) throw new Error('Invalid payment request ID.');
  const origin = new URL(base);
  if (origin.protocol !== 'https:' || origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash) throw new Error('A public HTTPS origin is required for a share card.');
  return `${origin.origin}/p/${p.id}`;
}
export function cardAlt(p: Payment) {
  return `Payment request: ${p.amount} ZEC for @${p.recipient}. ${p.network === 'testnet' ? 'TESTNET, no real value.' : 'MAINNET.'} QR opens the payment page. Payment unverified. Bot fee 0 ZEC; wallet network fees apply. Valid until ${new Date(p.expires).toISOString()}. Use the link in the reply on this device.`;
}
export function paymentCard(p: Payment, base: string, now = Date.now()): Buffer {
  // Render only stored, validated requests; never accept arbitrary text or URLs from mentions.
  paymentUri(p);
  if (!/^[A-Za-z0-9_]{1,15}$/.test(p.recipient) || !Number.isSafeInteger(p.expires) || p.expires <= now) throw new Error('Invalid or expired card request.');
  const url = paymentPageUrl(p, base);
  const cv = new Canvas();
  cv.rect(44, 42, 47, 47, AMBER, 24); cv.text('Z', 54, 76, 32, INK);
  cv.text('ZEC TIP BOT', 106, 75, 20, INK);
  cv.text(p.network === 'testnet' ? 'TESTNET / NO REAL VALUE' : 'ZCASH / MAINNET', 800, 75, 16, MUTED, 340);
  cv.rect(52, 126, 231, 34, [230, 234, 225], 17); cv.text('PAYMENT REQUEST', 69, 149, 14, INK);
  const end = cv.text(p.amount, 49, 286, 108, INK, 570); cv.text('ZEC', Math.round(end + 16), 284, 29, MUTED, 75);
  cv.text('A little appreciation for', 53, 338, 22, MUTED);
  cv.text('@' + p.recipient, 51, 391, 39, INK, 660);
  cv.rect(53, 430, 375, 54, AMBER, 10); cv.text('Open reply link to review and pay', 72, 464, 19, INK);
  cv.text('Payment unverified. Approve in your wallet.', 53, 523, 19, MUTED);
  cv.rect(770, 116, 378, 429, INK, 22);
  cv.rect(796, 141, 326, 326, WHITE, 8);
  const qr = QRCode.create(url, { errorCorrectionLevel: 'M' });
  const modules = qr.modules.size, unit = Math.floor(310 / (modules + 8));
  const start = 796 + Math.floor((326 - modules * unit) / 2);
  const top = 141 + Math.floor((326 - modules * unit) / 2);
  for (let y = 0; y < modules; y++) for (let x = 0; x < modules; x++) if (qr.modules.get(y, x)) cv.rect(start + x * unit, top + y * unit, unit, unit, INK);
  cv.text('SCAN TO OPEN REQUEST', 816, 510, 17, WHITE, 300);
  cv.rect(53, 567, 1094, 1, LINE);
  cv.text('BOT FEE 0 ZEC  /  WALLET NETWORK FEES APPLY', 53, 603, 14, MUTED, 660);
  cv.text('Valid until ' + new Date(p.expires).toISOString().replace('.000', ''), 53, 638, 14, MUTED, 650);
  cv.text(new URL(base).hostname.slice(0, 48), 770, 602, 14, MUTED, 375);
  cv.text('REQUEST ' + p.id.slice(0, 8).toUpperCase(), 770, 638, 14, MUTED);
  return cv.png();
}
