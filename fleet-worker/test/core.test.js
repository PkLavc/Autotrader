import test from "node:test";
import assert from "node:assert/strict";
import { estimateCostsAndTax, orderPayload, parseCookies, planRebalance, validateSignal } from "../src/core.js";

const limits = {
  maxOrderNotional: 100,
  maxTotalNotional: 180,
  rebalanceThreshold: 0.03,
  maxTargetWeight: 0.18,
  maxGrossTarget: 0.90,
};

test("plans sells before buys and never spends unavailable cash", () => {
  const orders = planRebalance({
    equity: 2000,
    cash: 80,
    positions: [{ symbol: "OLD", qty: 10, marketValue: 1000, currentPrice: 100 }],
    targets: [{ symbol: "NEW", weight: 0.18 }],
    signalDate: "2026-10-12",
    limits,
    executionCost: { prices: { NEW: 20 } },
  });
  assert.deepEqual(orders.map(order => order.side), ["sell", "buy"]);
  assert.equal(orders[1].notional, 80);
  assert.ok(orders.reduce((sum, order) => sum + order.estimatedNotional, 0) <= 180);
});

test("does not sell an asset absent from the account", () => {
  const orders = planRebalance({ equity: 1000, cash: 1000, positions: [], targets: [], signalDate: "2026-10-12", limits });
  assert.deepEqual(orders, []);
});

test("order identifiers are deterministic and payloads are broker-compatible", () => {
  const args = { equity: 1000, cash: 1000, positions: [], targets: [{ symbol: "AAPL", weight: 0.18 }], signalDate: "2026-10-12", limits, executionCost: { prices: { AAPL: 250 } } };
  const first = planRebalance(args)[0];
  const second = planRebalance(args)[0];
  assert.equal(first.clientOrderId, second.clientOrderId);
  assert.equal(orderPayload(first).notional, "100.00");
});

test("does not manufacture a price when a buy signal lacks reference prices", () => {
  const orders = planRebalance({
    equity: 1000, cash: 1000, positions: [], targets: [{ symbol: "AAPL", weight: 0.18 }],
    signalDate: "2026-10-12", limits,
  });
  assert.deepEqual(orders, []);
});

test("uses Alpaca-like zero commission and explicit sell regulatory fee estimates", () => {
  const orders = planRebalance({
    equity: 1000, cash: 0,
    positions: [{ symbol: "AAPL", qty: 10, marketValue: 1000, currentPrice: 100, avgEntryPrice: 80 }],
    targets: [], signalDate: "2026-10-12", limits,
  });
  const costTax = estimateCostsAndTax({ orders, taxProfile: "brazil_foreign_investments_estimate" });
  assert.equal(orders[0].commission, 0);
  assert.equal(orders[0].secFee, 0.01);
  assert.equal(orders[0].tafFee, 0.01);
  assert.equal(orders[0].catFee, 0.01);
  assert.ok(orders[0].estimatedRealizedGain > 19);
  assert.equal(costTax.estimatedTaxDue, null);
  assert.match(costTax.taxStatus, /pending_brl_fx/);
  assert.equal(costTax.taxReferenceRate, 0.15);
  assert.equal(costTax.taxCurrency, "BRL");
});

test("rejects stale and incomplete signals", () => {
  assert.throws(() => validateSignal({ targets: [] }, 7, new Date("2026-10-13T12:00:00Z")), /market_session/);
  assert.throws(() => validateSignal({ market_session: "2026-09-01", targets: [] }, 7, new Date("2026-10-13T12:00:00Z")), /stale/);
});

test("parses the session cookie without decoding or logging it", () => {
  assert.deepEqual(parseCookies("other=x; at_session=secret.value"), { other: "x", at_session: "secret.value" });
});
