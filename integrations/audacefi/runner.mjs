// AudaceFi official SDK connection. No schedule, implicit execution, or private-key loader.
import { readFile } from 'node:fs/promises';
import { resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const repoRoot = resolve(fileURLToPath(new URL('../../', import.meta.url)));
const kitPath = resolve(repoRoot, 'vendor/audacefi-integration-kit/dist/index.js');
const action = process.argv[2] || 'state';
const allowed = new Set(['state', 'execute', 'finalize']);
if (!allowed.has(action)) throw new Error('UNSUPPORTED_ACTION');
const kit = await import(pathToFileURL(kitPath).href);
const { AudaceFiClient, SolanaRpcReader, createReferenceAdapters, FileJournal, runAiAgent, finalizeOperation } = kit;
const key = process.env.AUDACEFI_API_KEY;
const origin = process.env.AUDACEFI_API_ORIGIN || 'https://sandbox.audacefi.app';
const category = process.env.AUDACEFI_CATEGORY || 'crypto-bro';
if (!key) throw new Error('AUDACEFI_ONBOARDING_KEY_REQUIRED');
if (action !== 'state' && origin === 'https://sandbox.audacefi.app') {
  // The official /api proxy refuses write-enabled generic clients. Ranked
  // execution requires the separately issued, signed official approval.
  throw new Error('SDK_OFFICIAL_RANKED_GATEWAY_AND_SIGNED_APPROVAL_REQUIRED');
}
const client = new AudaceFiClient({
  origin,
  credential: async () => key,
  ...(action === 'state' ? { apiBasePath: '/api' } : { writes: 'explicitly-authorized' }),
});

if (action === 'state') {
  const state = await client.readState(category, 'real');
  console.log(JSON.stringify({
    action: 'state', mode: state.mode, category: state.category,
    seasonActive: state.season.active?.status === 'active', wallet: state.wallet.state,
    executionAuthorized: false,
  }, null, 2));
  process.exit(0);
}

if (process.env.AUDACEFI_ENABLE_EXECUTION !== 'true' ||
    process.env.AUDACEFI_DEVNET_ACK !== 'true' ||
    !process.argv.includes('--confirm')) {
  throw new Error('EXPLICIT_DEVNET_EXECUTION_APPROVAL_REQUIRED');
}

async function privateJson(envName) {
  const file = process.env[envName];
  if (!file) throw new Error(envName + '_REQUIRED');
  return JSON.parse(await readFile(resolve(file), 'utf8'));
}
const settings = await privateJson('AUDACEFI_SETTINGS_PATH');
const profile = await privateJson('AUDACEFI_PROFILE_PATH');
if (profile.apiOrigin !== origin ||
    profile.apiApproval?.protocol !== 'audacefi.bench.submission.v1') {
  throw new Error('PROFILE_APPROVAL_REQUIRED');
}
const journalDir = resolve(settings.journalDirectory || '.');
if (journalDir === repoRoot || journalDir.startsWith(repoRoot + sep)) {
  throw new Error('JOURNAL_MUST_BE_OUTSIDE_REPOSITORY');
}
if (!settings.policy?.inputBudgets || !settings.policy?.maxOperations) {
  throw new Error('DURABLE_JOURNAL_POLICY_REQUIRED');
}
// GitHub Actions uses the registered Phantom address signer; no local PC needed.
const signerPath = process.env.AUDACEFI_SIGNER_MODULE || 'integrations/audacefi/wallet-signer.mjs';
const { wallet } = await import(pathToFileURL(resolve(signerPath)).href);
if (!wallet || typeof wallet.publicKey !== 'function' ||
    typeof wallet.signTransaction !== 'function') {
  throw new Error('INVALID_WALLET_ADAPTER');
}
const rpcEndpoint = process.env.AUDACEFI_RPC_ENDPOINT;
if (!rpcEndpoint) throw new Error('QUALIFIED_DEVNET_RPC_REQUIRED');
const rpc = new SolanaRpcReader({ endpoint: rpcEndpoint });
const adapters = createReferenceAdapters({ profile, client, rpc, wallet });
const journal = await FileJournal.create(journalDir, settings.policy);

if (action === 'finalize') {
  const operationKey = process.argv[3];
  if (!operationKey || operationKey.startsWith('--')) throw new Error('OPERATION_KEY_REQUIRED');
  const result = await finalizeOperation(client, journal, operationKey, adapters.verifyFinal);
  console.log(JSON.stringify({ action, result }, null, 2));
} else {
  const operation = await privateJson('AUDACEFI_OPERATION_PATH');
  const toUsdcRaw = (value) => {
    const s = String(value);
    if (!/^[0-9]+(?:[.][0-9]{1,6})?$/.test(s)) throw new Error('INVALID_USDC_LIMIT');
    const parts = s.split('.');
    return BigInt(parts[0] + (parts[1] || '').padEnd(6, '0'));
  };
  const amount = BigInt(operation.amountInRaw);
  if (amount !== toUsdcRaw(settings.buyAmountUsdc) ||
      amount > toUsdcRaw(settings.maxInputUsdc) ||
      operation.amountIn !== Number(settings.buyAmountUsdc) ||
      operation.seasonId !== settings.seasonId ||
      operation.category !== settings.category ||
      operation.playerPublicKey !== settings.playerPublicKey ||
      operation.tokenInMint !== settings.usdcMainnet ||
      operation.inputDecimals !== 6 ||
      !Object.values(settings.tokens || {}).includes(operation.tokenOutMint) ||
      BigInt(operation.amountInRaw) > BigInt(settings.policy.inputBudgets[operation.tokenInMint] || '0')) {
    throw new Error('OPERATION_OUTSIDE_APPROVED_ALLOWLIST');
  }
  if (await wallet.publicKey() !== operation.playerPublicKey) {
    throw new Error('WALLET_MISMATCH');
  }
  const result = await runAiAgent({ client, journal, operation, ports: adapters.ports });
  console.log(JSON.stringify({ action, result }, null, 2));
}
