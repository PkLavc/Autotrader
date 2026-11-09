// Season 1 spot operator. All plans and risk state are stored in the encrypted
// journal branch. The stock AutoTrader is not imported or modified.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { readFile, writeFile, unlink } from 'node:fs/promises';
import { join } from 'node:path';
import { updateOpportunityLedger } from './opportunity-ledger.mjs';

const MAX_OPERATIONS = 40;
const PILOT_SWAPS = 2;
const PILOT_COUNTED_UNTIL = Date.parse('2026-11-10T05:00:00Z');
const HOUR = 3600_000;
const keyFor = index => `autotrader_s1_competitive_${String(Math.floor(index / 2) + 1).padStart(2, '0')}_${index % 2 ? 'sell' : 'buy'}`;
const planFile = (directory, index) => join(directory, `operation-${String(index).padStart(2, '0')}.json`);
const hold = reason => console.log('AUDACEFI_AUTO_HOLD=' + reason);

function decision(context, portfolio, adaptiveState) {
  const env = Object.fromEntries(['PATH', 'PYTHONPATH', 'LANG', 'LC_ALL', 'HOME', 'SSL_CERT_FILE']
    .filter(name => typeof process.env[name] === 'string').map(name => [name, process.env[name]]));
  env.PYTHONPATH = process.cwd();
  let historicalStrategy = 'momentum';
  try {
    const qualification = JSON.parse(readFileSync(
      'integrations/audacefi/strategy-qualification.json', 'utf8'));
    if (qualification?.schemaVersion === 2 &&
        Array.isArray(qualification?.approvedSymbols) &&
        ['SOL', 'ETH'].every(symbol => qualification.approvedSymbols.includes(symbol)) &&
        ['steady', 'early', 'recovery', 'breakout', 'mean_reversion', 'multi_trend',
          'momentum_loose', 'momentum_tight', 'breakout_confirmed',
          'mean_reversion_deep', 'multi_trend_strict'].includes(qualification?.approvedStrategy) &&
        Number.isInteger(qualification?.sourceRunId) && qualification.sourceRunId > 0)
      historicalStrategy = qualification.approvedStrategy;
  } catch {}
  const window = Math.floor(Date.now() / (15 * 60_000));
  if (Number.isInteger(adaptiveState?.historicalRollbackUntilWindow) &&
      window < adaptiveState.historicalRollbackUntilWindow)
    historicalStrategy = 'momentum';
  const raw = execFileSync('python', ['-m', 'integrations.audacefi.live_decision'], {
    input: JSON.stringify({ context, portfolio, fetchSignals: true,
      signalMode: 'intraday', adaptiveState,
      activeProfile: adaptiveState.active, historicalStrategy }),
    encoding: 'utf8', timeout: 90_000, maxBuffer: 65536, env,
  });
  const result = JSON.parse(raw);
  if (!['HOLD', 'PROPOSED', 'EXIT_REQUIRED'].includes(result.status) ||
      !Array.isArray(result.actions) || result.actions.length > 2)
    throw Error('AUTO_STRATEGY_RESPONSE_INVALID');
  return result;
}

async function readPlan(directory, index) {
  const row = JSON.parse(await readFile(planFile(directory, index), 'utf8'));
  if (row.index !== index || !Number.isInteger(row.createdAt) ||
      !['buy', 'sell'].includes(row.side) || !['SOL', 'ETH'].includes(row.symbol) ||
      row.operation?.operationKey !== keyFor(index)) throw Error('AUTO_PLAN_INVALID');
  return row;
}

async function save(directory, name, value, checkpoint) {
  await writeFile(join(directory, name), JSON.stringify(value) + '\n', { mode: 0o600 });
  await checkpoint();
}

export async function runAuto({ client, journal, journalDirectory, checkpoint, adapters,
  runAiAgent, reconcileOperation, finalizeOperation, get, season, expectedAddress, usdc, assets }) {
  if (process.env.AUDACEFI_ENABLE_EXECUTION !== 'true' || process.env.AUDACEFI_DEVNET_ACK !== 'true')
    throw Error('AUTO_EXECUTION_FLAGS_REQUIRED');
  if (assets?.length !== 2 || assets[0].symbol !== 'SOL' || assets[1].symbol !== 'ETH')
    throw Error('AUTO_ASSETS_INVALID');
  let capacity = await journal.capacity();
  const count = capacity.reservedOperations;
  if (count > MAX_OPERATIONS || capacity.runningOperations) throw Error('AUTO_CAPACITY_INVALID');
  const plans = [];
  for (let i = 0; i < count; i++) plans.push(await readPlan(journalDirectory, i));
  if (count) {
    const prior = await journal.read(keyFor(count - 1));
    const stage = prior.events.at(-1)?.stage;
    if (stage !== 'FINAL_CONFIRMED') {
      if (['FINAL_FAILED', 'ABANDONED', 'UNSENT', 'EXPIRED_UNSENT'].includes(stage))
        throw Error('AUTO_PREVIOUS_OPERATION_NOT_CONFIRMED');
      const saved = await reconcileOperation(client, journal, keyFor(count - 1));
      if (!saved) { hold('PENDING_SUBMISSION'); return; }
      const final = await finalizeOperation(client, journal, keyFor(count - 1), adapters.verifyFinal);
      await checkpoint();
      console.log('AUDACEFI_AUTO_RECONCILED=' + final);
      if (final !== 'confirmed') { hold('PENDING_FINALITY'); return; }
    }
  }
  capacity = await journal.capacity();
  if (capacity.unresolvedOperations) { hold('UNRESOLVED_OPERATION'); return; }
  const platform = await get('/v1/me/wallet?category=crypto_bro');
  if (platform.seasonId !== season.id || platform.walletAddress !== expectedAddress ||
      !Array.isArray(platform.holdings) ||
      platform.holdings.some(row => !['USDC', 'SOL', 'ETH'].includes(row.symbol)))
    throw Error('AUTO_PLATFORM_WALLET_INVALID');
  if (!Number.isInteger(platform.swapCount) || platform.swapCount > PILOT_SWAPS + count)
    throw Error('AUTO_UNRECONCILED_EXTERNAL_SWAP');
  if (platform.swapCount < PILOT_SWAPS + count) { hold('PLATFORM_INDEX_PENDING'); return; }
  const snapshots = await Promise.all(assets.map(asset => adapters.readSpotHoldings(expectedAddress, asset.mint)));
  const usdcRaw = BigInt(snapshots[0].usdcRaw);
  if (usdcRaw < 0n || BigInt(snapshots[1].usdcRaw) !== usdcRaw)
    throw Error('AUTO_CHAIN_USDC_MISMATCH');
  const balances = Object.fromEntries(assets.map((asset, i) => [asset.symbol, BigInt(snapshots[i].assetRaw)]));
  const hasPosition = count % 2 === 1;
  const buyPlan = hasPosition ? plans[count - 1] : null;
  const active = buyPlan ? assets.find(asset => asset.symbol === buyPlan.symbol) : null;
  if (hasPosition && (buyPlan.side !== 'buy' || !active)) throw Error('AUTO_ACTIVE_PLAN_INVALID');
  for (const asset of assets) {
    const expectedHeld = hasPosition && active.symbol === asset.symbol;
    const platformHeld = platform.holdings.some(row => row.symbol === asset.symbol && Number(row.amount) > 0);
    if ((balances[asset.symbol] > 0n) !== expectedHeld || platformHeld !== expectedHeld ||
        balances[asset.symbol] > 10_000n * 10n ** BigInt(asset.decimals))
      throw Error('AUTO_PLATFORM_CHAIN_HOLDINGS_MISMATCH');
  }
  const now = Date.now();
  const remaining = Date.parse(season.endsAt) - now;
  if (remaining <= 0) throw Error('AUTO_SEASON_ENDED');
  if (!hasPosition && count === MAX_OPERATIONS) { hold('LIFETIME_TRADE_BUDGET_COMPLETE'); return; }
  const cash = Number(usdcRaw) / 1e6;
  // Closed-loop feedback comes from reconciled, finalized Devnet holdings,
  // never from a hypothetical backtest or a model's claimed success.
  if (!hasPosition && count >= 2 && count % 2 === 0) {
    const pair = count / 2;
    const buy = plans[count - 2], sell = plans[count - 1];
    let feedback = { lastClosedPair: 0, outcomes: [] };
    try { feedback = JSON.parse(await readFile(join(journalDirectory, 'adaptive-performance.json'), 'utf8')); }
    catch (error) { if (error?.code !== 'ENOENT') throw error; }
    if (!Number.isInteger(feedback?.lastClosedPair) || feedback.lastClosedPair < 0 ||
        !Array.isArray(feedback.outcomes) || feedback.outcomes.length > 32)
      throw Error('ADAPTIVE_FEEDBACK_INVALID');
    if (feedback.lastClosedPair < pair) {
      if (buy?.side !== 'buy' || sell?.side !== 'sell') throw Error('ADAPTIVE_FEEDBACK_ORDER_INVALID');
      const before = buy.cashBefore;
      if (before !== undefined && (!Number.isFinite(before) || before <= 0))
        throw Error('ADAPTIVE_FEEDBACK_ENTRY_INVALID');
      if (before !== undefined) {
        const pnl = Math.round((cash - before) * 1e6) / 1e6;
        if (!Number.isFinite(pnl) || Math.abs(pnl) > 5000)
          throw Error('ADAPTIVE_FEEDBACK_PNL_INVALID');
        feedback.outcomes.push({ pair, pnl, profile: buy.profile || 'baseline' });
        console.log('AUDACEFI_ADAPTIVE_REALIZED_PNL_USDC=' + pnl);
      }
      // Older competitive trades predate cashBefore: mark as observed, never
      // fabricate an outcome or block later true accounting.
      feedback = { lastClosedPair: pair, outcomes: feedback.outcomes.slice(-24) };
      await save(journalDirectory, 'adaptive-performance.json', feedback, checkpoint);
      const recent = feedback.outcomes.slice(-3);
      if (recent.length === 3 && recent.every(row => row.pnl < 0) &&
          recent.reduce((sum, row) => sum + row.pnl, 0) < -20) {
        let active = { active: 'baseline' };
        try { active = JSON.parse(await readFile(join(journalDirectory, 'adaptive-state.json'), 'utf8')); }
        catch (error) { if (error?.code !== 'ENOENT') throw error; }
        if (active?.active !== 'baseline') {
          await save(journalDirectory, 'adaptive-state.json', {
            active: 'baseline', pending: null, count: 0,
            window: Math.floor(Date.now() / 900000),
          cooldownUntilWindow: Math.floor(Date.now() / 900000) + 96,
          historicalRollbackUntilWindow: Math.floor(Date.now() / 900000) + 96,
          }, checkpoint);
          console.log('AUDACEFI_ADAPTIVE_LIVE_ROLLBACK=true');
        }
      }
    }
  }
  const responses = await Promise.allSettled(assets.map(asset =>
    get(`/v1/market/prices?mints=${encodeURIComponent(asset.mint)}`)));
  const markets = Object.fromEntries(assets.map((asset, index) =>
    [asset.symbol, responses[index].status === 'fulfilled' ? responses[index].value : null]));
  const priced = asset => {
    const market = markets?.[asset.symbol];
    const value = market?.prices?.[asset.mint];
    const age = now - Date.parse(market?.asOf);
    const price = Number(value?.priceUsd);
    return value?.status === 'priced' && Number.isFinite(price) && price > 0 &&
      Number.isFinite(age) && age >= -60_000 && age <= 15 * 60_000 ? price : null;
  };
  const price = hasPosition ? priced(active) : null;
  const quantity = hasPosition ? Number(balances[active.symbol]) / 10 ** active.decimals : 0;
  const equity = hasPosition && price !== null ? cash + quantity * price : cash;
  if (!Number.isFinite(equity) || equity <= 0 || equity > 20_000)
    throw Error('AUTO_EQUITY_INVALID');
  let risk = null;
  try { risk = JSON.parse(await readFile(join(journalDirectory, 'risk-state.json'), 'utf8')); }
  catch (error) { if (error?.code !== 'ENOENT') throw error; }
  if (risk && (!Number.isFinite(risk.dailyPeak) || risk.dailyPeak < 0 ||
      !Number.isFinite(risk.seasonPeak) || risk.seasonPeak < risk.dailyPeak ||
      (risk.signalWindow !== undefined && (!Number.isInteger(risk.signalWindow) || risk.signalWindow < 0)) ||
      !/^\d{4}-\d{2}-\d{2}$/.test(risk.day))) throw Error('AUTO_RISK_STATE_INVALID');
  const day = new Date(now).toISOString().slice(0, 10);
  const dailyPeak = risk?.day === day ? Math.max(risk.dailyPeak, equity) : equity;
  const seasonPeak = Math.max(risk?.seasonPeak ?? 10_000, dailyPeak);
  await save(journalDirectory, 'risk-state.json', {
    day, dailyPeak, seasonPeak, ...(risk?.signalWindow !== undefined ? { signalWindow: risk.signalWindow } : {}),
  }, checkpoint);
  const riskStop = equity <= 9800 || equity <= dailyPeak * .982 || equity <= seasonPeak * .982;
  let peakPrice = price;
  if (hasPosition && price !== null) {
    const file = `peak-${Math.floor((count - 1) / 2) + 1}.json`;
    try {
      const old = JSON.parse(await readFile(join(journalDirectory, file), 'utf8'));
      if (Number.isFinite(old.price) && old.price > 0) peakPrice = Math.max(price, old.price);
    } catch (error) { if (error?.code !== 'ENOENT') throw error; }
    await save(journalDirectory, file, { price: peakPrice }, checkpoint);
  }
  const entryPrice = hasPosition ? buyPlan.operation.amountIn / quantity : null;
  const protectiveStop = hasPosition && price !== null &&
    price <= Math.max(entryPrice * .965, peakPrice * .975);
  if (!hasPosition && process.env.AUDACEFI_ALLOW_ENTRY !== 'true') {
    hold('STRATEGY_REVIEW_PAUSE'); return;
  }
  let side = null;
  let modelProfile = 'baseline';
  let chosen = active;
  let amount = null;
  if (hasPosition && (remaining <= 16 * HOUR || riskStop || protectiveStop || price === null))
    side = 'sell';
  else if (!hasPosition && (remaining <= 24 * HOUR || riskStop)) {
    hold(riskStop ? 'DRAWDOWN_GUARD' : 'ENTRY_WINDOW_CLOSED'); return;
  } else {
    const signalWindow = Math.floor(now / (15 * 60_000));
    if (risk?.signalWindow === signalWindow) { hold('WAITING_FOR_NEW_CANDLE'); return; }
    await save(journalDirectory, 'risk-state.json', { day, dailyPeak, seasonPeak, signalWindow }, checkpoint);
    const context = { verified: true, category: 'crypto-bro', restrictions_verified: true,
      wallet_ready: true, network: 'solana-devnet', season_status: 'active',
      start_utc: season.startsAt, end_utc: season.endsAt,
      eligible_symbols: assets.map(asset => asset.symbol), max_position_weight: .35,
      max_total_exposure: .35, start_snapshot_confirmed: true };
    const portfolio = { equity_usdc: equity, cash_usdc: cash, peak_equity_usdc: dailyPeak,
      positions: hasPosition ? { [active.symbol]: { value_usdc: quantity * price,
        price_usdc: price, entry_price_usdc: entryPrice,
        peak_price_usdc: Math.max(entryPrice, peakPrice) } } : {} };
    let adaptiveState = { active: 'baseline' };
    try {
      adaptiveState = JSON.parse(await readFile(join(journalDirectory, 'adaptive-state.json'), 'utf8'));
    } catch (error) { if (error?.code !== 'ENOENT') throw error; }
    if (!['baseline', 'steady', 'breakout', 'early', 'recovery', 'mean_reversion', 'multi_trend'].includes(adaptiveState?.active))
      throw Error('ADAPTIVE_STORED_PROFILE_INVALID');
    let result;
    try { result = decision(context, portfolio, adaptiveState); }
    catch { hold('STRATEGY_DATA_UNAVAILABLE'); return; }
    const nextState = result.adaptiveState;
    if (!nextState || !['baseline', 'steady', 'breakout', 'early', 'recovery', 'mean_reversion', 'multi_trend'].includes(nextState.active) ||
        !Number.isInteger(nextState.window) || nextState.window !== signalWindow ||
        !Number.isInteger(nextState.count) || nextState.count < 0 || nextState.count > 2 ||
        (nextState.pending !== null && !['steady', 'breakout', 'early', 'recovery', 'mean_reversion', 'multi_trend'].includes(nextState.pending)))
      throw Error('ADAPTIVE_STATE_INVALID');
    if (Number.isInteger(adaptiveState.historicalRollbackUntilWindow))
      nextState.historicalRollbackUntilWindow = adaptiveState.historicalRollbackUntilWindow;
    if (Number.isInteger(adaptiveState.cooldownUntilWindow))
      nextState.cooldownUntilWindow = adaptiveState.cooldownUntilWindow;
    let observations = [];
    try { observations = JSON.parse(await readFile(join(journalDirectory, 'adaptive-decisions.json'), 'utf8')); }
    catch (error) { if (error?.code !== 'ENOENT') throw error; }
    if (!Array.isArray(observations) || observations.length > 256) throw Error('ADAPTIVE_LOG_INVALID');
    observations.push({ window: signalWindow, status: result.status,
      reason: result.reason || 'SIGNAL', profile: result.activeProfile,
      candidate: result.adaptive?.profile || null,
      validation: result.adaptive?.status || 'NOT_AVAILABLE',
      ...(Array.isArray(result.assetDiagnostics) ? { assets: result.assetDiagnostics } : {}),
      ...(result.adaptive?.holdout ? { holdout: result.adaptive.holdout } : {}) });
    await writeFile(join(journalDirectory, 'adaptive-decisions.json'),
      JSON.stringify(observations.slice(-192)) + '\n', { mode: 0o600 });
    let opportunityRows = [];
    try { opportunityRows = JSON.parse(await readFile(
      join(journalDirectory, 'adaptive-opportunities.json'), 'utf8')); }
    catch (error) { if (error?.code !== 'ENOENT') throw error; }
    opportunityRows = updateOpportunityLedger(
      opportunityRows, result.assetDiagnostics || [], now);
    await save(journalDirectory, 'adaptive-opportunities.json', opportunityRows, checkpoint);
    await save(journalDirectory, 'adaptive-state.json', nextState, checkpoint);
    modelProfile = result.activeProfile;
    console.log('AUDACEFI_ADAPTIVE_STATUS=' + (result.adaptive?.status || 'NOT_AVAILABLE'));
    console.log('AUDACEFI_ADAPTIVE_ACTIVE=' + nextState.active);
    console.log('AUDACEFI_REJECTED_OPPORTUNITY_SAMPLES=' + opportunityRows.length);
    console.log('AUDACEFI_AUTO_DECISION=' + result.status);
    if (/^[A-Z_]{1,64}$/.test(result.reason ?? ''))
      console.log('AUDACEFI_AUTO_REASON=' + result.reason);
    if (hasPosition && result.actions.some(row => row.side === 'SELL_TO_USDC' && row.symbol === active.symbol))
      side = 'sell';
    if (!hasPosition && result.status === 'PROPOSED') {
      const proposal = result.actions.find(row => row.side === 'BUY_WITH_USDC' &&
        assets.some(asset => asset.symbol === row.symbol));
      if (proposal) {
        chosen = assets.find(asset => asset.symbol === proposal.symbol);
        const serverPrice = priced(chosen), reference = Number(proposal.reference_price_usd);
        if (serverPrice === null || !Number.isFinite(reference) || reference <= 0 ||
            Math.abs(serverPrice / reference - 1) > .05) {
          hold('MARKET_REFERENCE_MISMATCH'); return;
        }
        amount = Math.floor(Math.min(Number(proposal.maximum_input_usdc), 3500, cash * .35) * 100) / 100;
        if (!Number.isFinite(amount) || amount < 500) { hold('ENTRY_SIZE_TOO_SMALL'); return; }
        side = 'buy';
      }
    }
  }
  if (!side) { hold('STRATEGY_HOLD'); return; }
  if (count >= MAX_OPERATIONS || (side === 'buy' && count + 1 >= MAX_OPERATIONS))
    throw Error('AUTO_OPERATION_LIMIT_REACHED');
  const rolling = plans.filter(row => row.createdAt > now - 24 * HOUR).length +
    (now < PILOT_COUNTED_UNTIL ? PILOT_SWAPS : 0);
  if (side === 'buy' && (rolling > 8 || cash - amount < 6000 ||
      remaining <= 24 * HOUR || riskStop)) { hold('ENTRY_RISK_BOUND'); return; }
  if (side === 'sell' && rolling >= 10) { hold('DAILY_TRADE_LIMIT_EXIT_PENDING'); return; }
  const raw = side === 'buy' ? BigInt(Math.round(amount * 1e6)) : balances[active.symbol];
  if (raw <= 0n) throw Error('AUTO_ZERO_INPUT');
  const operation = { operationKey: keyFor(count), seasonId: season.id,
    category: 'crypto-bro', playerPublicKey: expectedAddress,
    tokenInMint: side === 'buy' ? usdc.mint : active.mint,
    tokenOutMint: side === 'buy' ? chosen.mint : usdc.mint,
    amountIn: side === 'buy' ? Number(raw) / 1e6 : Number(raw) / 10 ** active.decimals,
    amountInRaw: raw.toString(), inputDecimals: side === 'buy' ? 6 : active.decimals,
    limits: { outputDecimals: side === 'buy' ? chosen.decimals : 6, notAfterMs: now + 60_000 } };
  if (await journal.readIfReserved(keyFor(count))) throw Error('AUTO_JOURNAL_COUNT_CHANGED');
  try {
    await readFile(planFile(journalDirectory, count), 'utf8');
    // A missing reservation proves no AudaceFi write occurred; reserve precedes POST.
    await unlink(planFile(journalDirectory, count));
    await checkpoint();
  } catch (error) { if (error?.code !== 'ENOENT') throw error; }
  await writeFile(planFile(journalDirectory, count), JSON.stringify({
    index: count, side, symbol: chosen.symbol, createdAt: now, operation,
    ...(side === 'buy' ? { cashBefore: cash, profile: modelProfile } : {}),
  }) + '\n', { flag: 'wx', mode: 0o600 });
  await checkpoint();
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
