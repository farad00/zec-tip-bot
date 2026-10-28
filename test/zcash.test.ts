import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bech32, bech32m, createBase58check } from '@scure/base';
import { sha256 } from '@noble/hashes/sha2.js';
import { amountString, zatoshis, validateAddress, paymentUri } from '../src/zcash.js';
export const TEST_SAPLING = 'ztestsapling10yy2ex5dcqkclhc7z7yrnjq2z6feyjad56ptwlfgmy77dmaqqrl9gyhprdx59qgmsnyfska2kez';
export const MAIN_SAPLING = bech32.encode('zs', bech32.toWords(Buffer.from('d8ef8293d26de832e7193f296ba1922d90f122c6135bc231eebd91efdb03b1a8606771cd4fd6480574d43e', 'hex')), 200);
export const UA = 'u1l8xunezsvhq8fgzfl7404m450nwnd76zshscn6nfys7vyz2ywyh4cc5daaq0c7q2su5lqfh23sp7fkf3kt27ve5948mzpfdvckzaect2jtte308mkwlycj2u0eac077wu70vqcetkxf';
test('ZIP-321 known testnet example: exact amount, base64url memo and percent label', () => {
  assert.equal(paymentUri({ address: TEST_SAPLING, network: 'testnet', amount: '1', memo: 'This is a simple memo.', label: 'Alice & Bob' }), `zcash:${TEST_SAPLING}?amount=1&memo=VGhpcyBpcyBhIHNpbXBsZSBtZW1vLg&label=Alice%20%26%20Bob`);
});
test('decimal arithmetic is exact, including 1 zatoshi; reject unsafe/ambiguous inputs', () => {
  assert.equal(zatoshis('0.00000001'), 1n); assert.equal(amountString(zatoshis('001.10000000')), '1.1');
  assert.equal(amountString(zatoshis('10')), '10');
  for (const bad of ['0', '-1', '1e2', '.5', '5.', '0.123456789', 'NaN', '21000000.00000001', '1&amount=9']) assert.throws(() => zatoshis(bad));
  assert.throws(() => paymentUri({ address: MAIN_SAPLING, network: 'mainnet', amount: '1.1', max: '1' }));
});
test('transparent address checksum and network validation; memos rejected', () => {
  const address = createBase58check(sha256).encode(Buffer.concat([Buffer.from('1cb8', 'hex'), Buffer.alloc(20, 3)]));
  assert.equal(validateAddress(address, 'mainnet').kind, 'transparent');
  assert.throws(() => validateAddress(address, 'testnet'));
  assert.throws(() => paymentUri({ address, amount: '0.1', network: 'mainnet', memo: 'memo' }));
  assert.throws(() => validateAddress(address.slice(0, -1) + 'X', 'mainnet'));
});
test('official zcash-test-vectors Unified Address decodes with F4Jumble, not just prefix checks', () => {
  assert.equal(validateAddress(UA, 'mainnet').kind, 'unified');
  assert.throws(() => validateAddress(UA, 'testnet'));
  assert.throws(() => validateAddress(UA.slice(0, -1) + 'q', 'mainnet'));
  const forged = bech32m.encode('u', bech32m.toWords(Buffer.alloc(80, 1)), 2000);
  assert.throws(() => validateAddress(forged, 'mainnet'));
});
test('Sapling checksum, key/seed rejection, UTF-8 memo byte cap', () => {
  assert.equal(validateAddress(TEST_SAPLING, 'testnet').kind, 'sapling');
  assert.throws(() => validateAddress(TEST_SAPLING, 'mainnet'));
  assert.throws(() => validateAddress('secret-extended-key-main1abc', 'mainnet'));
  assert.throws(() => validateAddress('seed words here', 'mainnet'));
  assert.throws(() => paymentUri({ address: TEST_SAPLING, network: 'testnet', amount: '1', memo: '🙂'.repeat(129) }));
  assert.ok(paymentUri({ address: TEST_SAPLING, network: 'testnet', amount: '1', memo: '🙂'.repeat(128) }).includes('memo='));
});
