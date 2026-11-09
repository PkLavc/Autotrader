// Read-only chain qualification for the directly available AudaceFi bench.
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '../..');
const kit = resolve(root, 'vendor/audacefi-integration-kit/dist');
const { SolanaRpcReader, SOLANA_DEVNET_GENESIS } = await import(pathToFileURL(resolve(kit, 'index.js')).href);
const { benchAddresses } = await import(pathToFileURL(resolve(kit, 'bench.js')).href);
const { encode58, decode58 } = await import(pathToFileURL(resolve(kit, 'solana.js')).href);
const key = process.env.AUDACEFI_API_KEY;
if (!key) throw Error('API_KEY_REQUIRED');
const response = await fetch('https://sandbox.audacefi.app/api/v1/devnet/bench', {
  headers: { Authorization: `Bearer ${key}`, Accept: 'application/json' },
  redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(12000),
});
if (response.status !== 200) throw Error(`BENCH_HTTP_${response.status}`);
const bench = await response.json();
const address = value => decode58(value, 32);
if (bench.cluster !== 'devnet' || !Array.isArray(bench.mints) ||
    bench.mints.length < 2 || bench.mints.length > 64) throw Error('BENCH_INVALID');
for (const value of [bench.programId, bench.poster, bench.configPda,
  bench.vaultAuthority, bench.feeAuthority, bench.usdcMintMainnet, bench.usdcMintDevnet]) address(value);
const mappings = bench.mints.map(row => {
  address(row.mintMainnet); address(row.mintDevnet);
  if (!Number.isInteger(row.decimals) || row.decimals < 0 || row.decimals > 9) throw Error('MAPPING_INVALID');
  return { mainnet: row.mintMainnet, devnet: row.mintDevnet, decimals: row.decimals };
});
if (!mappings.some(row => row.mainnet === bench.usdcMintMainnet && row.devnet === bench.usdcMintDevnet && row.decimals === 6))
  throw Error('USDC_MAPPING_INVALID');
const derived = benchAddresses({ programId: bench.programId });
if (derived.config.address !== bench.configPda || derived.vault.address !== bench.vaultAuthority ||
    derived.fee.address !== bench.feeAuthority) throw Error('BENCH_PDA_MISMATCH');
const rpc = new SolanaRpcReader({ endpoint: 'https://api.devnet.solana.com', timeoutMs: 20000 });
if (await rpc.call('getGenesisHash', []) !== SOLANA_DEVNET_GENESIS) throw Error('DEVNET_GENESIS_MISMATCH');
const read = async keys => {
  const result = await rpc.call('getMultipleAccounts', [keys, { commitment: 'finalized', encoding: 'base64' }]);
  if (!Array.isArray(result?.value) || result.value.length !== keys.length) throw Error('RPC_ACCOUNTS_INVALID');
  return result.value.map(row => {
    if (!row || !Array.isArray(row.data) || row.data[1] !== 'base64') throw Error('RPC_ACCOUNT_MISSING');
    const bytes = Buffer.from(row.data[0], 'base64');
    if (bytes.toString('base64') !== row.data[0]) throw Error('RPC_ACCOUNT_ENCODING');
    return { ...row, bytes };
  });
};
const loader = 'BPFLoaderUpgradeab1e11111111111111111111111';
const tokenProgram = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA';
const [program, config] = await read([bench.programId, bench.configPda]);
if (program.owner !== loader || program.executable !== true || program.bytes.length !== 36 ||
    program.bytes.readUInt32LE(0) !== 2) throw Error('PROGRAM_INVALID');
const programData = encode58(program.bytes.subarray(4));
const [data, usdcMint] = await read([programData, bench.usdcMintDevnet]);
if (data.owner !== loader || data.bytes.length < 45 || data.bytes.readUInt32LE(0) !== 3)
  throw Error('PROGRAMDATA_INVALID');
const disc = createHash('sha256').update('account:BenchConfig').digest().subarray(0, 8);
if (config.owner !== bench.programId || config.bytes.length !== 115 ||
    !config.bytes.subarray(0, 8).equals(disc) ||
    encode58(config.bytes.subarray(40, 72)) !== bench.poster ||
    encode58(config.bytes.subarray(72, 104)) !== bench.usdcMintDevnet ||
    config.bytes.readBigInt64LE(104) !== BigInt(bench.freshnessSeconds)) throw Error('CONFIG_INVALID');
if (usdcMint.owner !== tokenProgram || usdcMint.bytes.length !== 82 || usdcMint.bytes[44] !== 6)
  throw Error('USDC_MINT_INVALID');
console.log('DIRECT_PROFILE_QUALIFIED=true');
console.log('DIRECT_PROGRAM_ID=' + bench.programId);
console.log('DIRECT_PROGRAMDATA_ID=' + programData);
console.log('DIRECT_PROGRAMDATA_SHA256=' + createHash('sha256').update(data.bytes).digest('hex'));
console.log('DIRECT_ADMIN=' + encode58(config.bytes.subarray(8, 40)));
