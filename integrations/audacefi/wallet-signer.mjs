/** GitHub Actions signer for registered Phantom Solana wallet. Node crypto only.
 * Keys are supplied by GitHub Actions secrets, never stored or logged here.
 * The official bench integration kit validates the plan before calling this port.
 */
import { createHmac, createPrivateKey, createPublicKey, pbkdf2Sync, sign, verify, timingSafeEqual } from 'node:crypto';

const B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';
const PKCS8_ED25519 = Buffer.from('302e020100300506032b657004220420', 'hex');

function b58encode(data) {
  let value = BigInt('0x' + Buffer.from(data).toString('hex'));
  let result = '';
  while (value > 0n) {
    const rem = Number(value % 58n);
    result = B58[rem] + result;
    value /= 58n;
  }
  for (const byte of data) { if (byte !== 0) break; result = '1' + result; }
  return result || '1';
}

function b58decode(text) {
  if (typeof text !== 'string' || !text || /[^1-9A-HJ-NP-Za-km-z]/.test(text)) {
    throw new Error('INVALID_EXPECTED_PUBLIC_KEY');
  }
  let number = 0n;
  for (const char of text) number = number * 58n + BigInt(B58.indexOf(char));
  let hex = number === 0n ? '' : number.toString(16);
  if (hex.length % 2) hex = '0' + hex;
  let data = hex ? Buffer.from(hex, 'hex') : Buffer.alloc(0);
  const zeroes = text.match(/^1*/)[0].length;
  data = Buffer.concat([Buffer.alloc(zeroes), data]);
  if (data.length !== 32 || b58encode(data) !== text) throw new Error('INVALID_EXPECTED_PUBLIC_KEY');
  return data;
}

function derivePath(seed, accountIndex, withChange) {
  const hardened = 0x80000000;
  const parts = [44, 501, accountIndex, ...(withChange ? [0] : [])];
  let privateKey = Buffer.alloc(0);
  try {
    let state = createHmac('sha512', 'ed25519 seed').update(seed).digest();
    privateKey = Buffer.from(state.subarray(0, 32));
    let chainCode = Buffer.from(state.subarray(32));
    state.fill(0);
    for (const index of parts) {
      const value = Buffer.alloc(4);
      value.writeUInt32BE(index + hardened);
      state = createHmac('sha512', chainCode)
        .update(Buffer.alloc(1)).update(privateKey).update(value).digest();
      privateKey.fill(0);
      chainCode.fill(0);
      privateKey = Buffer.from(state.subarray(0, 32));
      chainCode = Buffer.from(state.subarray(32));
      state.fill(0);
    }
    chainCode.fill(0);
    return privateKey;
  } catch (error) {
    privateKey.fill(0);
    throw error;
  }
}

function keyPairForSeed(seed32) {
  const privateDer = Buffer.concat([PKCS8_ED25519, seed32]);
  try {
    const privateKey = createPrivateKey({key: privateDer, format: 'der', type: 'pkcs8'});
    const publicDer = createPublicKey(privateKey).export({format: 'der', type: 'spki'});
    return { privateKey, publicKey: Buffer.from(publicDer.subarray(-32)) };
  } finally { privateDer.fill(0); }
}

function readShortVec(bytes, start) {
  let value = 0;
  let offset = start;
  for (let n = 0; n < 3; n++) {
    if (offset >= bytes.length) throw new Error('INVALID_LEGACY_TRANSACTION');
    const byte = bytes[offset++];
    value += (byte & 0x7f) * 2 ** (7 * n);
    if (!(byte & 0x80)) return {value, next: offset};
  }
  throw new Error('INVALID_LEGACY_TRANSACTION');
}

function unsignedLegacyMessage(txBytes, ownerKey) {
  const header = readShortVec(txBytes, 0);
  if (header.value !== 2) throw new Error('EXPECTED_TWO_LEGACY_SIGNERS');
  const signatureOffset = header.next;
  const messageOffset = signatureOffset + header.value * 64;
  if (messageOffset + 3 + 1 + 32 > txBytes.length) throw new Error('INVALID_LEGACY_TRANSACTION');
  if (txBytes[messageOffset] !== 2 || txBytes[messageOffset] & 0x80) {
    throw new Error('INVALID_LEGACY_SIGNER_HEADER');
  }
  const accountCount = readShortVec(txBytes, messageOffset + 3);
  if (accountCount.value < 2 || accountCount.value > 64) throw new Error('INVALID_LEGACY_ACCOUNTS');
  const payerStart = accountCount.next;
  if (payerStart + accountCount.value * 32 > txBytes.length) throw new Error('INVALID_LEGACY_ACCOUNTS');
  const payer = txBytes.subarray(payerStart, payerStart + 32);
  if (!timingSafeEqual(payer, ownerKey)) throw new Error('SIGNER_PAYER_MISMATCH');
  const slots = txBytes.subarray(signatureOffset, messageOffset);
  if (slots.some(byte => byte !== 0)) throw new Error('PREEXISTING_SIGNATURE_REFUSED');
  return {signatureOffset, message: txBytes.subarray(messageOffset)};
}

export function createWallet({ mnemonic, expectedPublicKey }) {
  if (typeof mnemonic !== 'string' || ![12, 24].includes(mnemonic.trim().split(/\s+/u).length)) {
    throw new Error('INVALID_PHANTOM_RECOVERY_PHRASE');
  }
  const expected = b58decode(expectedPublicKey);
  const seed = pbkdf2Sync(mnemonic.normalize('NFKD').trim().replace(/\s+/gu, ' '),
    'mnemonic', 2048, 64, 'sha512');
  let signingKey = null;
  try {
    for (let index = 0; index < 10 && !signingKey; index++) {
      for (const withChange of [true, false]) {
        const child = derivePath(seed, index, withChange);
        try {
          const candidate = keyPairForSeed(child);
          if (timingSafeEqual(candidate.publicKey, expected)) {
            signingKey = candidate.privateKey;
            break;
          }
        } finally {child.fill(0);}
      }
    }
  } finally {seed.fill(0);}
  if (!signingKey) throw new Error('PHANTOM_WALLET_ADDRESS_MISMATCH');
  return {
    async publicKey() { return expectedPublicKey; },
    async signTransaction(unsignedBase64) {
      if (typeof unsignedBase64 !== 'string' || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(unsignedBase64)) {
        throw new Error('INVALID_UNSIGNED_TRANSACTION');
      }
      const bytes = Buffer.from(unsignedBase64, 'base64');
      if (!bytes.length || bytes.length > 1232) throw new Error('INVALID_TRANSACTION_PACKET_SIZE');
      const {signatureOffset, message} = unsignedLegacyMessage(bytes, expected);
      const signature = sign(null, message, signingKey);
      if (signature.length !== 64 || !verify(null, message, createPublicKey(signingKey), signature)) {
        throw new Error('ED25519_SIGNATURE_FAILURE');
      }
      signature.copy(bytes, signatureOffset);
      return bytes.toString('base64');
    },
  };
}

let signer;
function loadSigner() {
  if (!signer) signer = createWallet({
    mnemonic: process.env.AUDACEFI_WALLET_RECOVERY_PHRASE,
    expectedPublicKey: process.env.AUDACEFI_WALLET_ADDRESS,
  });
  return signer;
}

export const wallet = {
  publicKey: async () => loadSigner().publicKey(),
  signTransaction: async (unsignedBase64) => {
    if (process.env.AUDACEFI_ENABLE_EXECUTION !== 'true' ||
        process.env.AUDACEFI_DEVNET_ACK !== 'true') {
      throw new Error('WALLET_EXECUTION_DISABLED');
    }
    return loadSigner().signTransaction(unsignedBase64);
  },
};
