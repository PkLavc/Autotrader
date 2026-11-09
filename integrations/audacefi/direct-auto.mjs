// Fixed-budget Season 1 operator. One live position, at most three 25-USDC
// round trips. The journal, platform wallet and chain must agree before entry.
import { execFileSync } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const MAX_OPERATIONS = 6;
const INITIAL_PLATFORM_SWAPS = 2;
const POSITION_USDC = 25;
const ENTRY_CUTOFF_MS = 24 * 3600_000;
const EXIT_BUFFER_MS = 16 * 3600_000;
const keyFor = index => `autotrader_s1_auto_${String(Math.floor(index / 2) + 1).padStart(2, '0')}_${index % 2 ? 'sell' : 'buy'}`;
const hold = reason => console.log('AUDACEFI_AUTO_HOLD=' + reason);

function decideWithExistingStrategy(context, portfolio, fetchSignals) {
  const env = Object.fromEntries(['PATH', 'PYTHONPATH', 'LANG', 'LC_ALL', 'HOME', 'SSL_CERT_FILE']
    .filter(key => typeof process.env[key] === 'string').map(key => [key, process.env[key]]));
  env.PYTHONPATH = process.cwd();
  const output = execFileSync('python', ['-m', 'integrations.audacefi.live_decision'], {
    input: JSON.stringify({ context, portfolio, fetchSignals }), encoding: 'utf8',
    timeout: 90_000, maxBuffer: 65536, env,
  });
  const decision = JSON.parse(output);
  if (!['HOLD', 'PROPOSED', 'EXIT_REQUIRED'].includes(decision.status) ||
      !Array.isArray(decision.actions) || decision.actions.length > 3)
    throw Error('AUTO_STRATEGY_RESPONSE_INVALID');
  return decision;
}

export async function runAuto({ client, journal, journalDirectory, checkpoint, adapters,
  runAiAgent, reconcileOperation, finalizeOperation, get, season, expectedAddress, usdc, sol }) {
  if (process.env.AUDACEFI_ENABLE_EXECUTION !== 'true' || process.env.AUDACEFI_DEVNET_ACK !== 'true')
    throw Error('AUTO_EXECUTION_FLAGS_REQUIRED');
  let capacity = await journal.capacity();
  const count = capacity.reservedOperations;
  if (count > MAX_OPERATIONS || capacity.runningOperations !== 0) throw Error('AUTO_JOURNAL_CAPACITY_INVALID');
  if (count) {
    const key = keyFor(count - 1);
    const record = await journal.read(key);
    const stage = record.events.at(-1)?.stage;
    if (stage !== 'FINAL_CONFIRMED') {
      if (stage === 'FINAL_FAILED' || stage === 'ABANDONED' || stage === 'UNSENT')
        throw Error('AUTO_PREVIOUS_OPERATION_NOT_CONFIRMED');
      const saved = await reconcileOperation(client, journal, key);
      if (!saved) { hold('PENDING_SUBMISSION'); return; }
      const final = await finalizeOperation(client, journal, key, adapters.verifyFinal);
      await checkpoint();
      console.log('AUDACEFI_AUTO_RECONCILED=' + final);
      if (final !== 'confirmed') { hold('PENDING_FINALITY'); return; }
    }
  }
  capacity = await journal.capacity();
  if (capacity.unresolvedOperations !== 0) { hold('UNRESOLVED_OPERATION'); return; }
  const platform = await get('/v1/me/wallet?category=crypto_bro');
  if (platform.seasonId !== season.id || platform.walletAddress !== expectedAddress ||
      !Array.isArray(platform.holdings) ||
      platform.holdings.some(item => !['USDC', 'SOL'].includes(item.symbol)))
    throw Error('AUTO_PLATFORM_WALLET_INVALID');
  const expectedSwaps = INITIAL_PLATFORM_SWAPS + count;
  if (!Number.isInteger(platform.swapCount) || platform.swapCount > expectedSwaps)
    throw Error('AUTO_UNRECONCILED_EXTERNAL_SWAP');
  if (platform.swapCount < expectedSwaps) { hold('PLATFORM_INDEX_PENDING'); return; }
  const chain = await adapters.readSpotHoldings(expectedAddress, sol.mint);
  const solRaw = BigInt(chain.assetRaw), usdcRaw = BigInt(chain.usdcRaw);
  const hasPosition = count % 2 === 1;
  const platformSol = platform.holdings.some(item => item.symbol === 'SOL' && Number(item.amount) > 0);
  if ((solRaw > 0n) !== hasPosition || platformSol !== hasPosition ||
      usdcRaw < 0n || solRaw > 10_000_000_000n)
    throw Error('AUTO_PLATFORM_CHAIN_HOLDINGS_MISMATCH');
  const endMs = Date.parse(season.endsAt);
  const remainingMs = endMs - Date.now();
  const lpv = Number(platform.lastLpv?.lpvUsdc);
  const riskStop = Number.isFinite(lpv) && lpv > 0 && lpv <= 9800;
  if (count === MAX_OPERATIONS && !hasPosition) { hold('LIFETIME_TRADE_BUDGET_COMPLETE'); return; }
  if (!hasPosition && (remainingMs <= ENTRY_CUTOFF_MS || riskStop)) {
    hold(riskStop ? 'DRAWDOWN_GUARD' : 'ENTRY_WINDOW_CLOSED'); return;
  }
  let price = null;
  if (remainingMs > EXIT_BUFFER_MS) {
    const market = await get(`/v1/market/prices?mints=${encodeURIComponent(sol.mint)}`);
    const value = market.prices?.[sol.mint];
    const age = Date.now() - Date.parse(market.asOf);
    price = Number(value?.priceUsd);
    if (value?.status !== 'priced' || !Number.isFinite(price) || price <= 0 ||
        !Number.isFinite(age) || age < -60_000 || age > 15 * 60_000) {
      hold('CURRENT_PRICE_UNAVAILABLE'); return;
    }
  }
  const cash = Number(usdcRaw) / 1e6;
  const quantity = Number(solRaw) / 1e9;
  const positionValue = hasPosition ? quantity * price : 0;
  const equity = cash + positionValue;
  if (!Number.isFinite(equity) || equity < 9800 || equity > 10200) {
    if (!hasPosition) { hold('PORTFOLIO_RISK_BOUND'); return; }
  }
  let peakPrice = price;
  if (hasPosition && price !== null) {
    const file = join(journalDirectory, `peak-sol-${Math.floor((count - 1) / 2) + 1}.json`);
    try {
      const prior = JSON.parse(await readFile(file, 'utf8'));
      if (Number.isFinite(prior.price) && prior.price > 0) peakPrice = Math.max(price, prior.price);
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
    }
    await writeFile(file, JSON.stringify({ price: peakPrice }) + '\n', { mode: 0o600 });
    await checkpoint();
  }
  const context = {
    verified: true, category: 'crypto-bro', restrictions_verified: true,
    wallet_ready: true, network: 'solana-devnet', season_status: 'active',
    start_utc: season.startsAt, end_utc: season.endsAt,
    eligible_symbols: ['SOL'], max_position_weight: POSITION_USDC / Math.max(equity, 1),
    max_total_exposure: POSITION_USDC / Math.max(equity, 1), start_snapshot_confirmed: true,
  };
  const entry = hasPosition ? POSITION_USDC / quantity : 0;
  const portfolio = {
    equity_usdc: equity, cash_usdc: cash, peak_equity_usdc: Math.max(10_000, equity),
    positions: hasPosition && price !== null ? {
      SOL: { value_usdc: positionValue, price_usdc: price,
        entry_price_usdc: entry, peak_price_usdc: Math.max(entry, peakPrice) },
    } : {},
  };
  let side = null;
  if (hasPosition && (remainingMs <= EXIT_BUFFER_MS || riskStop)) side = 'sell';
  else {
    let decision;
    try { decision = decideWithExistingStrategy(context, portfolio, !hasPosition); }
    catch { hold('STRATEGY_DATA_UNAVAILABLE'); return; }
    console.log('AUDACEFI_AUTO_DECISION=' + decision.status);
    if (hasPosition && decision.actions.some(item => item.side === 'SELL_TO_USDC' && item.symbol === 'SOL'))
      side = 'sell';
    if (!hasPosition && decision.status === 'PROPOSED' &&
        decision.actions.some(item => item.side === 'BUY_WITH_USDC' && item.symbol === 'SOL'))
      side = 'buy';
  }
  if (!side) { hold('STRATEGY_HOLD'); return; }
  if (count >= MAX_OPERATIONS || side !== (hasPosition ? 'sell' : 'buy'))
    throw Error('AUTO_OPERATION_SEQUENCE_INVALID');
  if (side === 'buy' && (cash < POSITION_USDC || remainingMs <= ENTRY_CUTOFF_MS ||
      equity < 9850 || platform.swapCount >= 10)) { hold('ENTRY_RISK_BOUND'); return; }
  const operation = {
    operationKey: keyFor(count), seasonId: season.id, category: 'crypto-bro',
    playerPublicKey: expectedAddress,
    tokenInMint: side === 'buy' ? usdc.mint : sol.mint,
    tokenOutMint: side === 'buy' ? sol.mint : usdc.mint,
    amountIn: side === 'buy' ? POSITION_USDC : Number(solRaw) / 1e9,
    amountInRaw: side === 'buy' ? '25000000' : solRaw.toString(),
    inputDecimals: side === 'buy' ? 6 : 9,
    limits: { outputDecimals: side === 'buy' ? 9 : 6, notAfterMs: Date.now() + 60_000 },
  };
  let outcome;
  try { outcome = await runAiAgent({ client, journal, operation, ports: adapters.ports }); }
  catch (error) {
    await checkpoint();
    const code = /^[A-Z_]{1,64}$/.test(error?.code) ? error.code :
      (/^[A-Z_]{1,64}$/.test(error?.message) ? error.message : 'UNCLASSIFIED');
    throw Error('AUTO_ORDER_ABORTED_' + code);
  }
  await checkpoint();
  console.log('AUDACEFI_AUTO_SUBMISSION=' + outcome.state);
  if (outcome.state !== 'SUBMITTED' && outcome.state !== 'RECORDED') { hold('SUBMISSION_NOT_FINAL'); return; }
  for (let attempt = 0; attempt < 4; attempt++) {
    if (attempt) await new Promise(resolve => setTimeout(resolve, 5000));
    const final = await finalizeOperation(client, journal, keyFor(count), adapters.verifyFinal);
    await checkpoint();
    if (final !== 'unknown') {
      console.log('AUDACEFI_AUTO_FINALITY=' + final);
      if (final === 'confirmed') console.log('AUDACEFI_AUTO_TXID=' + outcome.txid);
      return;
    }
  }
  hold('PENDING_FINALITY');
}
