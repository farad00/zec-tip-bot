import { bech32, bech32m, createBase58check } from '@scure/base';
import { sha256 } from '@noble/hashes/sha2.js';
import { blake2b } from '@noble/hashes/blake2.js';
export type Network = 'mainnet' | 'testnet';
export type AddressInfo = { kind: 'transparent' | 'sapling' | 'unified'; memo: boolean; address: string; transparentReceiver: boolean };
export function zatoshis(value: string): bigint {
  if (value.length > 32 || !/^\d+(\.\d{1,8})?$/.test(value)) throw new Error('Use a decimal ZEC amount with at most 8 decimal places; no signs or exponents.');
  const [whole = '', fraction = ''] = value.split('.');
  const result = BigInt(whole) * 100_000_000n + BigInt(fraction.padEnd(8, '0'));
  if (result <= 0n || result > 21_000_000n * 100_000_000n) throw new Error('Amount is outside the supported positive ZEC range.');
  return result;
}
export function amountString(n: bigint): string {
  return `${n / 100_000_000n}.${(n % 100_000_000n).toString().padStart(8, '0')}`.replace(/\.?0+$/, '');
}
function unjumble(input: Uint8Array): Uint8Array {
  if (input.length < 48 || input.length > 1024) throw new Error('Unsupported Unified Address length.');
  const leftLength = Math.min(64, Math.floor(input.length / 2));
  const a = Uint8Array.from(input.subarray(0, leftLength));
  const b = Uint8Array.from(input.subarray(leftLength));
  const xor = (target: Uint8Array, mask: Uint8Array, offset = 0) => {
    for (let j = 0; j < mask.length && j + offset < target.length; j++) target[j + offset] = target[j + offset]! ^ mask[j]!;
  };
  const h = (i: number) => xor(a, blake2b(b, { dkLen: a.length, personalization: Buffer.concat([Buffer.from('UA_F4Jumble_H'), Buffer.from([i, 0, 0])]) }));
  const g = (i: number) => {
    for (let j = 0; j * 64 < b.length; j++) xor(b, blake2b(a, { dkLen: 64, personalization: Buffer.concat([Buffer.from('UA_F4Jumble_G'), Buffer.from([i, j & 255, j >> 8])]) }), j * 64);
  };
  h(1); g(1); h(0); g(0);
  return Buffer.concat([a, b]);
}
function unified(address: string, network: Network): AddressInfo {
  const { prefix, words } = bech32m.decode(address as `${string}1${string}`, 2000);
  // Revision 0 only. Draft revisions and viewing/spending keys are rejected.
  if (prefix !== (network === 'mainnet' ? 'u' : 'utest')) throw new Error('Unsupported Unified Address network or revision.');
  const raw = Buffer.from(unjumble(bech32m.fromWords(words)));
  const padding = Buffer.alloc(16); padding.write(prefix);
  if (!raw.subarray(-16).equals(padding)) throw new Error('Invalid Unified Address padding.');
  const items = raw.subarray(0, -16);
  let offset = 0, previous = -1, transparent = false, shielded = false;
  const readSize = (): number => {
    const head = items[offset++];
    if (head === undefined) throw new Error('Truncated receiver.');
    if (head < 253) return head;
    const width = head === 253 ? 2 : head === 254 ? 4 : 8;
    if (offset + width > items.length || width === 8) throw new Error('Invalid receiver size.');
    const n = items.readUIntLE(offset, width); offset += width;
    if (n < (width === 2 ? 253 : 65536) || n > 0x2000000) throw new Error('Noncanonical receiver size.');
    return n;
  };
  while (offset < items.length) {
    const type = readSize(), length = readSize();
    if (type <= previous || offset + length > items.length) throw new Error('Invalid receiver order or length.');
    previous = type;
    if (type <= 1) {
      if (transparent || length !== 20) throw new Error('Invalid transparent receiver.');
      transparent = true;
    } else if (type === 2 || type === 3) {
      if (length !== 43) throw new Error('Invalid shielded receiver.');
      shielded = true;
    } else {
      // Keep the MVP deliberately strict: no experimental receivers or metadata.
      throw new Error('This MVP accepts only standard revision 0 UA receivers.');
    }
    offset += length;
  }
  if (!shielded) throw new Error('A Unified Address must include a shielded receiver.');
  return { kind: 'unified', memo: true, address: address.toLowerCase(), transparentReceiver: transparent };
}
export function validateAddress(address: string, network: Network): AddressInfo {
  if (address.length > 2000 || !/^[a-zA-Z0-9]+$/.test(address)) throw new Error('Invalid Zcash receiving address.');
  try {
    if (/^(u1|utest1)/i.test(address)) return unified(address, network);
    if (/^(zs1|ztestsapling1)/i.test(address)) {
      const { prefix, words } = bech32.decode(address as `${string}1${string}`, 200);
      if (prefix !== (network === 'mainnet' ? 'zs' : 'ztestsapling') || bech32.fromWords(words).length !== 43) throw new Error();
      return { kind: 'sapling', memo: true, address: address.toLowerCase(), transparentReceiver: false };
    }
    const bytes = createBase58check(sha256).decode(address);
    const version = Buffer.from(bytes.subarray(0, 2)).toString('hex');
    if (bytes.length !== 22 || !(network === 'mainnet' ? ['1cb8', '1cbd'] : ['1d25', '1cba']).includes(version)) throw new Error();
    return { kind: 'transparent', memo: false, address, transparentReceiver: true };
  } catch {
    throw new Error('Address checksum, network, or format is invalid/unsupported. Use a standard Unified, Sapling, or transparent receiving address; never a key or seed.');
  }
}
export function paymentUri(input: { address: string; amount: string; network: Network; memo?: string; label?: string; max?: string }): string {
  const address = validateAddress(input.address, input.network);
  const n = zatoshis(input.amount);
  if (input.max && n > zatoshis(input.max)) throw new Error('Amount exceeds MAX_TIP_ZEC.');
  const params = [`amount=${amountString(n)}`];
  if (input.memo !== undefined) {
    const memo = Buffer.from(input.memo, 'utf8');
    if (!address.memo || memo.length > 512) throw new Error('Memos need a shielded receiver and at most 512 UTF-8 bytes.');
    params.push(`memo=${memo.toString('base64url')}`);
  }
  if (input.label !== undefined) params.push(`label=${encodeURIComponent(input.label)}`);
  return `zcash:${address.address}?${params.join('&')}`;
}

