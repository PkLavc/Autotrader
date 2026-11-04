import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createPublicKey, verify } from 'node:crypto';
import { createWallet } from './wallet-signer.mjs';

const example = 'abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about';
const expected = 'HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk';

function unsignedTx() {
  const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
  let n = 0n;
  for (const c of expected) n = n * 58n + BigInt(B58.indexOf(c));
  const payerBytes = Buffer.from(n.toString(16).padStart(64, '0'), 'hex');
  assert.equal(payerBytes.length, 32);
  const message = Buffer.concat([Buffer.from([2, 0, 0, 2]), payerBytes, Buffer.alloc(32, 7), Buffer.alloc(32, 9), Buffer.from([0])]);
  return Buffer.concat([Buffer.from([2]), Buffer.alloc(128), message]);
}

test('derives first Solana account from public BIP39 test vector', async () => {
  const wallet = createWallet({mnemonic: example, expectedPublicKey: expected});
  assert.equal(await wallet.publicKey(), expected);
});

test('signs only payer signature, preserves cosigner slot and message', async () => {
  const wallet = createWallet({mnemonic: example, expectedPublicKey: expected});
  const input = unsignedTx();
  const output = Buffer.from(await wallet.signTransaction(input.toString('base64')), 'base64');
  assert.deepEqual(output.subarray(65), input.subarray(65));
  assert.notDeepEqual(output.subarray(1,65), Buffer.alloc(64));
  const signature = output.subarray(1,65);
  const publicKey = createPublicKey({key: Buffer.concat([
    Buffer.from('302a300506032b6570032100', 'hex'), output.subarray(133, 165)
  ]), format: 'der', type: 'spki'});
  assert.ok(verify(null, output.subarray(129), publicKey, signature));
});

test('rejects wrong address, reused signature and unsupported transaction', async () => {
  assert.throws(
    () => createWallet({mnemonic: example, expectedPublicKey: '11111111111111111111111111111111'}),
    /PHANTOM_WALLET_ADDRESS_MISMATCH/
  );
  const wallet = createWallet({mnemonic: example, expectedPublicKey: expected});
  const input = unsignedTx();
  input[1] = 1;
  await assert.rejects(wallet.signTransaction(input.toString('base64')), /PREEXISTING_SIGNATURE_REFUSED/);
  input[0] = 1;
  await assert.rejects(wallet.signTransaction(input.toString('base64')), /EXPECTED_TWO_LEGACY_SIGNERS/);
});
