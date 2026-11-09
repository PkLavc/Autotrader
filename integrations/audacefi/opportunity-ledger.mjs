// Encrypted-journal feedback for signals that the live risk/strategy filters
// rejected. These are counterfactual marks only; they never submit orders.
const HORIZONS = Object.freeze({ '1h': 60 * 60_000, '6h': 6 * 60 * 60_000,
  '24h': 24 * 60 * 60_000 });
const COST = 0.003;
const MAX_ARCHIVE = 4000;

function validRow(row) {
  return row && typeof row === 'object' && Number.isInteger(row.observedAt) &&
    Number.isFinite(row.referencePriceUsd) && row.referencePriceUsd > 0 &&
    typeof row.symbol === 'string' && /^[A-Z0-9]{2,12}$/.test(row.symbol) &&
    typeof row.reason === 'string' && row.reason.length <= 256 &&
    row.outcomes && typeof row.outcomes === 'object' && !Array.isArray(row.outcomes);
}

export function updateOpportunityLedger(previous, diagnostics, now = Date.now()) {
  if (!Array.isArray(previous) || previous.length > 250 ||
      !Array.isArray(diagnostics) || diagnostics.length > 20 ||
      !Number.isInteger(now) || now < 0 || previous.some(row => !validRow(row)))
    throw Error('OPPORTUNITY_LEDGER_INVALID');
  const rows = previous.map(row => ({ ...row, outcomes: { ...row.outcomes } }));
  const prices = new Map();
  for (const signal of diagnostics) {
    const price = Number(signal?.referencePriceUsd);
    const at = Date.parse(signal?.observedAt);
    if (typeof signal?.symbol === 'string' && Number.isFinite(price) && price > 0 &&
        Number.isFinite(at) && at <= now + 60_000)
      prices.set(signal.symbol, { price, at });
  }
  for (const row of rows) {
    const mark = prices.get(row.symbol);
    if (!mark || mark.at < row.observedAt) continue;
    for (const [horizon, delay] of Object.entries(HORIZONS)) {
      if (row.outcomes[horizon] !== undefined || mark.at - row.observedAt < delay) continue;
      const grossReturn = mark.price / row.referencePriceUsd - 1;
      row.outcomes[horizon] = {
        grossReturn: Number(grossReturn.toFixed(8)),
        netReturnAfterEstimatedCosts: Number((grossReturn - COST).toFixed(8)),
        markedAt: mark.at,
      };
    }
  }
  const latestWindow = new Set(rows.map(row =>
    `${row.symbol}:${Math.floor(row.observedAt / (15 * 60_000))}`));
  for (const signal of diagnostics) {
    if (!signal ||
        typeof signal.symbol !== 'string' || typeof signal.reason !== 'string') continue;
    const observedAt = Date.parse(signal.observedAt);
    const referencePriceUsd = Number(signal.referencePriceUsd);
    if (!Number.isFinite(observedAt) || observedAt > now + 60_000 ||
        !Number.isFinite(referencePriceUsd) || referencePriceUsd <= 0) continue;
    const key = `${signal.symbol}:${Math.floor(observedAt / (15 * 60_000))}`;
    if (latestWindow.has(key)) continue;
    latestWindow.add(key);
    rows.push({ symbol: signal.symbol,
      observedAt: Math.floor(observedAt / (15 * 60_000)) * (15 * 60_000),
      referencePriceUsd, strategy: String(signal.historicalStrategyApproval || 'momentum'),
      strategySelected: typeof signal.strategySelected === 'string' ? signal.strategySelected : null,
      reason: signal.reason.slice(0, 256), filters: signal.filters || {},
      indicators: signal.indicators && typeof signal.indicators === 'object'
        ? signal.indicators : {}, outcomes: {} });
  }
  return rows.slice(-200);
}

// The operational ledger stays small. Matured labels are copied to a separate
// encrypted-journal archive before rotation, keyed by symbol and observation
// bucket so reruns are idempotent.
export function mergeMaturedArchive(previous, ledger) {
  if (!Array.isArray(previous) || previous.length > MAX_ARCHIVE ||
      !Array.isArray(ledger) || ledger.length > 250)
    throw Error('OPPORTUNITY_ARCHIVE_INVALID');
  const archive = new Map();
  for (const row of previous) {
    const outcome = row?.outcomes?.['24h'];
    if (!row || !Number.isInteger(row.observedAt) ||
        !/^[A-Z0-9]{2,12}$/.test(row.symbol || '') ||
        !outcome || !Number.isInteger(outcome.markedAt) ||
        !Number.isFinite(outcome.netReturnAfterEstimatedCosts))
      throw Error('OPPORTUNITY_ARCHIVE_ROW_INVALID');
  }
  for (const row of ledger) if (!validRow(row)) throw Error('OPPORTUNITY_ARCHIVE_ROW_INVALID');
  for (const row of [...previous, ...ledger]) {
    const outcome = row.outcomes['24h'];
    if (!outcome || !Number.isInteger(outcome.markedAt) ||
        !Number.isFinite(outcome.netReturnAfterEstimatedCosts)) continue;
    const key = `${row.symbol}:${row.observedAt}`;
    archive.set(key, { symbol: row.symbol, observedAt: row.observedAt,
      strategy: row.strategy, strategySelected: row.strategySelected,
      indicators: row.indicators, outcomes: { '24h': outcome } });
  }
  return [...archive.values()].sort((a, b) => a.observedAt - b.observedAt)
    .slice(-MAX_ARCHIVE);
}
