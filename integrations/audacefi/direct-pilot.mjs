// One fixed 25-USDC Devnet pilot through the direct API and the kit's checked
// bench builder/finality verifier. The stock Autotrader is not imported.
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { openDurableJournal } from './durable-journal.mjs';
import { createDirectClient } from './direct-client.mjs';

const kitPath = resolve(import.meta.dirname, '../../vendor/audacefi-integration-kit/dist/index.js');
const { FileJournal, SolanaRpcReader, createReferenceAdapters, runAiAgent,
  reconcileOperation, finalizeOperation, SOLANA_DEVNET_GENESIS } = await import(pathToFileURL(kitPath).href);
const { wallet } = await import('./wallet-signer.mjs');

const ORIGIN = 'https://sandbox.audacefi.app';
const PROGRAM = '5VHyUtobwi2gcBbYQCfwQnguhpnVYPyWQdKuuKmDQKzp';
const PROGRAM_DATA = 'CjiQe8dV8xUgxRm6MPCuvmQdYQTtugvRTDR4LhVRpdCj';
const PROGRAM_DATA_SHA = '321fc0efa1b7329b84da3613c82fef0d0d68e621c177084f5bbb23be79aa25a5';
const ADMIN = 'DiMwgbncNbPKAznQrQ24Dk6TB97NWatetRG3Ptfx7Psk';
const OPERATION_KEY = 'autotrader_s1_pilot_buy_sol_25_v2';
const strBoolean = value => String(Boolean(value));
const mode = process.argv[2];
if (!['preflight', 'execute', 'reconcile', 'verify10'].includes(mode)) throw Error('PILOT_MODE_REQUIRED');
if (mode === 'execute' && process.env.AUDACEFI_ENABLE_EXECUTION !== 'true')
  throw Error('EXECUTION_FLAG_REQUIRED');

const key = process.env.AUDACEFI_API_KEY;
const expectedAddress = process.env.AUDACEFI_WALLET_ADDRESS;
if (!key || !expectedAddress) throw Error('PILOT_SECRETS_REQUIRED');
async function get(path) {
  const response = await fetch(ORIGIN + '/api' + path, {
    headers: { Authorization: `Bearer ${key}`, Accept: 'application/json' },
    redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(12000),
  });
  if (!response.ok) throw Error(`DIRECT_GET_HTTP_${response.status}`);
  return response.json();
}
const me = await get('/v1/me');
const accountId = me?.account?.id;
const client = createDirectClient(key, accountId);
await client.assertExecuteScope();
const state = await client.readState('crypto-bro', 'real');
const season = state.season.active;
const now = Date.now();
if (!season || season.status !== 'active' || season.executionMode !== 'devnet_onchain' ||
    !season.startsAt || !season.endsAt || Date.parse(season.startsAt) > now ||
    Date.parse(season.endsAt) <= now + 16 * 3600_000 ||
    state.wallet.state !== 'ok' || state.wallet.walletAddress !== expectedAddress ||
    state.wallet.seasonId !== season.id) throw Error('PILOT_SEASON_OR_WALLET_INVALID');
const allow = await client.readAllowlist('crypto-bro', season.id);
const token = symbol => {
  const rows = allow.tokens.filter(row => row.symbol === symbol && (row.eligible || row.exempt));
  if (rows.length !== 1 || !Number.isInteger(rows[0].decimals)) throw Error('PILOT_TOKEN_NOT_ELIGIBLE');
  return rows[0];
};
const usdc = token('USDC'), sol = token('SOL');
if (usdc.decimals !== 6 || sol.decimals !== 9) throw Error('PILOT_TOKEN_DECIMALS_INVALID');
const bench = await get('/v1/devnet/bench');
if (bench.cluster !== 'devnet' || bench.programId !== PROGRAM ||
    bench.usdcMintMainnet !== usdc.mint || !Array.isArray(bench.mints) ||
    bench.mints.length > 2048) throw Error('PILOT_BENCH_INVALID');
const mappings = [usdc, sol].map(item => {
  const rows = bench.mints.filter(row => row.mintMainnet === item.mint);
  if (rows.length !== 1 || rows[0].decimals !== item.decimals) throw Error('PILOT_MAPPING_INVALID');
  return { mainnet: item.mint, devnet: rows[0].mintDevnet, decimals: item.decimals };
});
if (bench.usdcMintDevnet !== mappings[0].devnet) throw Error('PILOT_USDC_MAPPING_INVALID');
const profile = {
  apiOrigin: ORIGIN, apiBasePath: '/api', expectedGenesis: SOLANA_DEVNET_GENESIS,
  programId: PROGRAM, programData: PROGRAM_DATA, programDataSha256: PROGRAM_DATA_SHA,
  admin: ADMIN, poster: bench.poster, freshnessSeconds: bench.freshnessSeconds,
  usdcMainnet: usdc.mint, mappings, singleSwapFeeBps: 20,
  // Operator attestation for this separately checked direct API pilot. This
  // is not the official ranked gateway's AudaceFi-signed approval package.
  apiApproval: { protocol: 'audacefi.bench.submission.v1',
    validFromMs: now - 10000, expiresAtMs: now + 30 * 60_000 },
};
const rpc = new SolanaRpcReader({ endpoint: 'https://api.devnet.solana.com', timeoutMs: 20000 });
const adapters = createReferenceAdapters({ profile, client, rpc, wallet });
await adapters.ports.verifyTarget();
if (await wallet.publicKey() !== expectedAddress) throw Error('PILOT_SIGNER_MISMATCH');
console.log('DIRECT_PILOT_CHAIN_AND_WALLET_READY=true');

const journalStore = await openDurableJournal('/tmp/audacefi-journal', {
  repository: process.env.GITHUB_REPOSITORY,
  githubToken: process.env.GITHUB_TOKEN,
  encryptionKey: process.env.AUDACEFI_JOURNAL_KEY,
  mainSha: process.env.GITHUB_SHA,
});
const policy = { maxOperations: 3, inputBudgets: {
  [usdc.mint]: '25000000', [sol.mint]: '10000000000',
} };
const journal = await FileJournal.create(journalStore.directory, policy);
const { checkpoint } = await journalStore.wrap(journal);
const capacity = await journal.capacity();
console.log('DIRECT_PILOT_JOURNAL_READY=true');
console.log('DIRECT_PILOT_RESERVED_OPERATIONS=' + capacity.reservedOperations);
if (mode === 'preflight') {
  console.log('DIRECT_PILOT_PREFLIGHT_ONLY=true');
  process.exit(0);
}
if (mode === 'reconcile' || mode === 'verify10') {
  if (capacity.reservedOperations !== 1) throw Error('PILOT_JOURNAL_RESERVATION_UNEXPECTED');
  const saved = await reconcileOperation(client, journal, OPERATION_KEY);
  if (!saved) {
    console.log('DIRECT_PILOT_SERVER_SUBMISSION=absent');
    process.exitCode = 1;
  } else {
    console.log('DIRECT_PILOT_SERVER_SUBMISSION=' + saved.state);
    console.log('DIRECT_PILOT_TXID=' + saved.txid);
    const chain = await rpc.call('getSignatureStatuses', [[saved.txid], { searchTransactionHistory: true }]);
    const row = chain?.value?.[0];
    console.log('DIRECT_PILOT_RPC_SIGNATURE_KNOWN=' + strBoolean(row !== null && row !== undefined));
    if (row) {
      console.log('DIRECT_PILOT_RPC_CONFIRMATION=' + (row.confirmationStatus ?? 'unknown'));
      console.log('DIRECT_PILOT_RPC_ERROR=' + strBoolean(row.err !== null));
    }
    const tx = await rpc.call('getTransaction', [saved.txid, {
      commitment: 'finalized', encoding: 'base64', maxSupportedTransactionVersion: 0,
    }]);
    console.log('DIRECT_PILOT_RPC_TRANSACTION_PRESENT=' + strBoolean(tx !== null));
    if (tx) {
      console.log('DIRECT_PILOT_RPC_TRANSACTION_VERSION=' + (tx.version ?? 'missing'));
      console.log('DIRECT_PILOT_RPC_TRANSACTION_ERROR=' + strBoolean(tx.meta?.err !== null));
      console.log('DIRECT_PILOT_RPC_TOKEN_BALANCES_PRESENT=' + strBoolean(
        Array.isArray(tx.meta?.preTokenBalances) && Array.isArray(tx.meta?.postTokenBalances)
      ));
    }
    try {
      const evidence = await client.readRankedEvidence(saved.intentId);
      console.log('DIRECT_PILOT_RANKED_EVIDENCE=' + (
        evidence?.evidence?.trade?.txid === saved.txid ? 'matched' : 'pending'
      ));
    } catch (error) {
      console.log('DIRECT_PILOT_RANKED_EVIDENCE=' + (/^HTTP_[0-9]{3}$/.test(error?.code)
        ? error.code : 'unavailable'));
    }
    if (mode === 'verify10') {
      const proof = await adapters.verifyFinal(saved, {
        genesisHash: SOLANA_DEVNET_GENESIS, origin: ORIGIN, apiBasePath: '/api',
      });
      console.log('DIRECT_PILOT_TEN_BPS_VERIFICATION=' + proof.status);
      process.exit(proof.status === 'confirmed' ? 0 : 1);
    }
    const final = await finalizeOperation(client, journal, OPERATION_KEY, adapters.verifyFinal);
    await checkpoint();
    console.log('DIRECT_PILOT_FINALITY=' + final);
    if (final !== 'confirmed') process.exitCode = 1;
  }
  process.exit();
}
if (capacity.reservedOperations !== 0 || capacity.unresolvedOperations !== 0)
  throw Error('PILOT_EXISTING_OPERATION_REQUIRES_RECONCILIATION');
const operation = {
  operationKey: OPERATION_KEY, seasonId: season.id, category: 'crypto-bro',
  playerPublicKey: expectedAddress, tokenInMint: usdc.mint, tokenOutMint: sol.mint,
  amountIn: 25, amountInRaw: '25000000', inputDecimals: 6,
  limits: { outputDecimals: 9, notAfterMs: Date.now() + 60_000 },
};
let outcome;
try {
  outcome = await runAiAgent({ client, journal, operation, ports: adapters.ports });
  await checkpoint();
} catch (error) {
  await checkpoint();
  throw Error('DIRECT_PILOT_ABORTED_' + (error?.code ?? (String(error?.message).match(/^[A-Z_]{1,64}$/)?.[0] ?? 'UNCLASSIFIED')));
}
console.log('DIRECT_PILOT_SUBMISSION_STATE=' + outcome.state);
if (outcome.state !== 'SUBMITTED' && outcome.state !== 'RECORDED') {
  console.log('DIRECT_PILOT_NO_CONFIRMED_TRADE=true');
  process.exitCode = 1;
} else {
  let final = 'unknown';
  for (let attempt = 0; attempt < 8 && final === 'unknown'; attempt++) {
    if (attempt) await new Promise(resolve => setTimeout(resolve, 5000));
    final = await finalizeOperation(client, journal, OPERATION_KEY, adapters.verifyFinal);
    await checkpoint();
  }
  console.log('DIRECT_PILOT_FINALITY=' + final);
  if (final === 'confirmed') {
    console.log('DIRECT_PILOT_TXID=' + outcome.txid);
    try {
      const evidence = await client.readRankedEvidence(outcome.intentId);
      console.log('DIRECT_PILOT_RANKED_EVIDENCE=' + (
        evidence?.evidence?.trade?.txid === outcome.txid ? 'matched' : 'pending'
      ));
    } catch { console.log('DIRECT_PILOT_RANKED_EVIDENCE=unavailable'); }
  } else process.exitCode = 1;
}
