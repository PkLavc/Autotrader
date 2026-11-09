import test from 'node:test';
import assert from 'node:assert/strict';
import { updateOpportunityLedger } from '../integrations/audacefi/opportunity-ledger.mjs';

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

test('never logs an executed proposal as a rejected opportunity', () => {
  const t0 = Date.parse('2026-11-09T00:00:00Z');
  const result = updateOpportunityLedger([], [{ symbol: 'ETH',
    observedAt: new Date(t0).toISOString(), referencePriceUsd: 2000,
    reason: 'ORDER_PROPOSED' }], t0);
  assert.deepEqual(result, []);
});

test('rejects malformed persisted state', () => {
  assert.throws(() => updateOpportunityLedger([{ symbol: 'SOL' }], [], 1),
    /OPPORTUNITY_LEDGER_INVALID/);
});
