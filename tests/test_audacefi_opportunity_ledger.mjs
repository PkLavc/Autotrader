import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeMaturedArchive, updateOpportunityLedger } from '../integrations/audacefi/opportunity-ledger.mjs';

test('records rejected signals once and marks their later net outcomes', () => {
  const t0 = Date.parse('2026-11-09T00:00:00Z');
  const diagnostic = { symbol: 'SOL', observedAt: new Date(t0).toISOString(),
    referencePriceUsd: 100, reason: 'momentum_6h_min',
    historicalStrategyApproval: 'momentum', filters: { momentum_6h_min: false } };
  const first = updateOpportunityLedger([], [diagnostic], t0);
  assert.equal(first.length, 1);
  assert.deepEqual(updateOpportunityLedger(first, [diagnostic], t0), first);
  const marked = updateOpportunityLedger(first, [{ ...diagnostic,
    observedAt: new Date(t0 + 6 * 3600_000).toISOString(), referencePriceUsd: 102,
    reason: 'momentum_6h_min' }], t0 + 6 * 3600_000);
  assert.equal(marked[0].outcomes['6h'].grossReturn, .02);
  assert.equal(marked[0].outcomes['6h'].netReturnAfterEstimatedCosts, .017);
  assert.equal(marked[0].outcomes['1h'].grossReturn, .02);
});

test('logs proposed and rejected opportunities for later counterfactual labels', () => {
  const t0 = Date.parse('2026-11-09T00:00:00Z');
  const result = updateOpportunityLedger([], [{ symbol: 'ETH',
    observedAt: new Date(t0).toISOString(), referencePriceUsd: 2000,
    reason: 'ORDER_PROPOSED' }], t0);
  assert.equal(result.length, 1);
  assert.equal(result[0].reason, 'ORDER_PROPOSED');
});

test('does not mark a future outcome before the requested horizon matures', () => {
  const t0 = Date.parse('2026-11-09T00:00:00Z');
  const diagnostic = { symbol: 'ETH', observedAt: new Date(t0).toISOString(),
    referencePriceUsd: 2000, reason: 'SIGNAL', indicators: { momentum_1h: .01 } };
  const first = updateOpportunityLedger([], [diagnostic], t0);
  const early = updateOpportunityLedger(first, [{ ...diagnostic,
    observedAt: new Date(t0 + 30 * 60_000).toISOString(), referencePriceUsd: 2010 }],
    t0 + 30 * 60_000);
  assert.deepEqual(early[0].outcomes, {});
});

test('rejects malformed persisted state', () => {
  assert.throws(() => updateOpportunityLedger([{ symbol: 'SOL' }], [], 1),
    /OPPORTUNITY_LEDGER_INVALID/);
});

test('matured outcomes survive active-ledger rotation and merge idempotently', () => {
  const start = Date.parse('2026-01-01T00:00:00Z');
  const rows = Array.from({ length: 200 }, (_, i) => ({
    symbol: i % 2 ? 'SOL' : 'ETH', observedAt: start + i * 86_400_000,
    referencePriceUsd: 100, reason: 'FILTERED', indicators: {}, outcomes: {
      '24h': { markedAt: start + (i + 1) * 86_400_000,
        netReturnAfterEstimatedCosts: .01 },
    },
  }));
  const first = mergeMaturedArchive([], rows);
  const newer = { ...rows[0], observedAt: start + 200 * 86_400_000 };
  const second = mergeMaturedArchive(first, [newer, ...rows.slice(-199)]);
  assert.equal(first.length, 200);
  assert.equal(second.length, 201);
  assert.equal(mergeMaturedArchive(second, [newer]).length, 201);
  assert.equal(second[0].observedAt, rows[0].observedAt);
});
