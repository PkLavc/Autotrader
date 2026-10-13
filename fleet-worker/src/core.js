const SYMBOL = /^[A-Z0-9.-]{1,15}$/;

export function validateLimits(limits) {
  const result = {
    maxOrderNotional: Number(limits.maxOrderNotional),
    maxTotalNotional: Number(limits.maxTotalNotional),
    rebalanceThreshold: Number(limits.rebalanceThreshold),
    maxTargetWeight: Number(limits.maxTargetWeight),
    maxGrossTarget: Number(limits.maxGrossTarget),
  };
  if (!(result.maxOrderNotional > 0) || !(result.maxTotalNotional > 0)
      || !(result.rebalanceThreshold >= 0 && result.rebalanceThreshold < 1)
      || !(result.maxTargetWeight > 0 && result.maxTargetWeight <= 1)
      || !(result.maxGrossTarget > 0 && result.maxGrossTarget <= 1)) {
    throw new Error("invalid account risk limits");
  }
  return result;
}

export function validateSignal(signal, maxAgeDays = 7, now = new Date()) {
  if (!signal || typeof signal !== "object" || !Array.isArray(signal.targets)) {
    throw new Error("signal must contain a targets array");
  }
  const signalDate = String(signal.market_session || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(signalDate)) {
    throw new Error("signal has no valid market_session");
  }
  const date = new Date(`${signalDate}T00:00:00Z`);
  const today = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const age = Math.floor((today - date) / 86400000);
  if (!Number.isFinite(age) || age < 0 || age > maxAgeDays) {
    throw new Error(`signal ${signalDate} is stale or invalid`);
  }
  return { signalDate, targets: signal.targets };
}

export function validateTargets(targets, limits) {
  const result = new Map();
  for (const target of targets) {
    const symbol = String(target?.symbol || "").trim().toUpperCase();
    const weight = Number(target?.weight);
    if (!SYMBOL.test(symbol) || !Number.isFinite(weight) || weight < 0
        || weight > limits.maxTargetWeight + 1e-12) {
      throw new Error(`unsafe target for ${symbol || "<empty>"}`);
    }
    if (result.has(symbol)) throw new Error(`duplicate target symbol: ${symbol}`);
    result.set(symbol, weight);
  }
  const gross = [...result.values()].reduce((sum, weight) => sum + weight, 0);
  if (gross > limits.maxGrossTarget + 1e-12) {
    throw new Error(`gross target exceeds limit: ${gross.toFixed(4)}`);
  }
  return result;
}

function orderId(signalDate, symbol, side) {
  return `at3-${signalDate.replaceAll("-", "")}-${symbol.toLowerCase()}-${side[0]}`.slice(0, 48);
}

export function planRebalance({ equity, cash, positions, targets, signalDate, limits: rawLimits, executionCost = {} }) {
  const limits = validateLimits(rawLimits);
  if (!(Number(equity) > 0) || !(Number(cash) >= 0)) throw new Error("invalid broker equity or cash");
  const weights = validateTargets(targets, limits);
  const held = new Map();
  for (const raw of positions) {
    const position = {
      symbol: String(raw.symbol).toUpperCase(),
      qty: Number(raw.qty),
      marketValue: Number(raw.marketValue),
      currentPrice: Number(raw.currentPrice),
      avgEntryPrice: Number(raw.avgEntryPrice || 0),
    };
    if (held.has(position.symbol)) throw new Error("broker returned duplicate positions");
    if (position.qty < 0 || position.marketValue < 0) throw new Error("short positions are not supported");
    held.set(position.symbol, position);
  }

  let remaining = limits.maxTotalNotional;
  const orders = [];
  const symbols = new Set([...held.keys(), ...weights.keys()]);
  for (const symbol of [...symbols].sort()) {
    const position = held.get(symbol);
    const current = position?.marketValue || 0;
    const desired = Number(equity) * (weights.get(symbol) || 0);
    const delta = desired - current;
    if (delta >= 0 || Math.abs(delta) / Number(equity) < limits.rebalanceThreshold) continue;
    if (!position || position.qty <= 0 || position.currentPrice <= 0) continue;
    const notional = Math.min(Math.abs(delta), limits.maxOrderNotional, remaining, current);
    const qty = Math.min(position.qty, notional / position.currentPrice);
    if (qty <= 0) continue;
    const estimatedNotional = qty * position.currentPrice;
    const commission = qty > 0 ? Math.min(
      Math.max(qty * Number(executionCost.commissionPerShare ?? 0), Number(executionCost.minimumCommission ?? 0)),
      estimatedNotional * 0.01,
    ) : 0;
    const feeRoundUpCent = value => Math.ceil(value * 100 - 1e-10) / 100;
    const secFee = feeRoundUpCent(estimatedNotional * Number(executionCost.secFeeRate ?? 0.0000206));
    const tafFee = feeRoundUpCent(Math.min(qty * Number(executionCost.tafFeePerShare ?? 0.000195), Number(executionCost.tafFeeCap ?? 9.79)));
    const catFee = feeRoundUpCent(qty * Number(executionCost.catFeePerShare ?? 0.000003));
    const estimatedRealizedGain = position.avgEntryPrice > 0
      ? qty * (position.currentPrice - position.avgEntryPrice) - commission - secFee - tafFee - catFee
      : null;
    orders.push({
      symbol, side: "sell", qty, estimatedNotional, commission, secFee, tafFee, catFee,
      estimatedRealizedGain, costBasisKnown: position.avgEntryPrice > 0,
      clientOrderId: orderId(signalDate, symbol, "sell"),
    });
    remaining -= estimatedNotional;
    if (remaining <= 0) return orders;
  }

  let availableCash = Number(cash);
  const buys = [...weights.entries()].sort((a, b) => b[1] - a[1]);
  for (const [symbol, weight] of buys) {
    const current = held.get(symbol)?.marketValue || 0;
    const delta = Number(equity) * weight - current;
    if (delta <= 0 || delta / Number(equity) < limits.rebalanceThreshold) continue;
    let notional = Math.floor(Math.min(delta, limits.maxOrderNotional, remaining, availableCash) * 100) / 100;
    if (notional < 1) continue;
    const referencePrice = Number(executionCost.prices?.[symbol] || 0);
    if (referencePrice <= 0) continue;
    let qty = notional / referencePrice;
    let commission = Math.min(
      Math.max(qty * Number(executionCost.commissionPerShare ?? 0), Number(executionCost.minimumCommission ?? 0)),
      notional * 0.01,
    );
    while (notional + commission > availableCash && notional >= 1) {
      notional = Math.floor((notional - 1) * 100) / 100;
      qty = notional / referencePrice;
      commission = Math.min(
        Math.max(qty * Number(executionCost.commissionPerShare ?? 0), Number(executionCost.minimumCommission ?? 0)),
        notional * 0.01,
      );
    }
    if (notional < 1) continue;
    orders.push({
      symbol, side: "buy", notional, estimatedNotional: notional, qty, commission,
      secFee: 0, tafFee: 0,
      catFee: Math.ceil(qty * Number(executionCost.catFeePerShare ?? 0.000003) * 100 - 1e-10) / 100,
      clientOrderId: orderId(signalDate, symbol, "buy"),
    });
    remaining -= notional;
    availableCash -= notional;
    if (remaining <= 0 || availableCash < 1) break;
  }
  return orders;
}

export function orderPayload(order) {
  const payload = {
    symbol: order.symbol,
    side: order.side,
    type: "market",
    time_in_force: "day",
    client_order_id: order.clientOrderId,
  };
  if (order.side === "buy" && order.notional != null) payload.notional = Number(order.notional).toFixed(2);
  else if (order.qty != null) payload.qty = Number(order.qty).toFixed(6).replace(/\.?0+$/, "");
  return payload;
}

export function estimateCostsAndTax({ orders, taxProfile, priorLossCarry = 0, taxRate = 0.15 }) {
  let commissions = 0;
  let secFees = 0;
  let tafFees = 0;
  let grossRealized = 0;
  let taxWithheldEstimate = 0;
  let catFees = 0;
  let missingCostBasis = false;
  for (const order of orders) {
    const qty = Number(order.qty || 0);
    const notional = Number(order.estimatedNotional || order.notional || 0);
    const variable = qty * Number(order.commissionPerShare ?? 0);
    const minimum = Number(order.minimumCommission ?? 0);
    const maxRate = 0.01;
    const commission = Number(order.commission ?? (qty > 0 && notional > 0 ? Math.min(Math.max(variable, minimum), notional * maxRate) : 0));
    const secFee = order.side === "sell" && notional > 0
      ? Number(order.secFee ?? Math.ceil(notional * Number(order.secFeeRate ?? 0.0000206) * 100 - 1e-10) / 100)
      : 0;
    const tafFee = order.side === "sell"
      ? Number(order.tafFee ?? Math.ceil(Math.min(qty * Number(order.tafFeePerShare ?? 0.000195), Number(order.tafFeeCap ?? 9.79)) * 100 - 1e-10) / 100)
      : 0;
    const catFee = Number(order.catFee ?? Math.ceil(qty * Number(order.catFeePerShare ?? 0.000003) * 100 - 1e-10) / 100);
    commissions += commission;
    secFees += secFee;
    tafFees += tafFee;
    catFees += catFee;
    if (order.side === "sell") {
      if (order.estimatedRealizedGain == null) missingCostBasis = true;
      else grossRealized += Number(order.estimatedRealizedGain);
    }
  }

  return {
    commissions,
    secFees,
    tafFees,
    catFees,
    regulatoryFees: secFees + tafFees + catFees,
    grossRealized,
    priorLossCarry: Number(priorLossCarry || 0),
    taxReferenceRate: taxProfile === "brazil_foreign_investments_estimate" ? taxRate : null,
    taxCurrency: taxProfile === "brazil_foreign_investments_estimate" ? "BRL" : null,
    taxNote: taxProfile === "brazil_foreign_investments_estimate"
      ? "Brazil tax due is not calculated. The 15% reference applies to the annual BRL tax base; event-date exchange rates and reconciled tax lots are required."
      : "US tax due is not calculated; it depends on taxpayer residence, holding period, income, and broker tax documents.",
    estimatedTax: null,
    estimatedTaxWithheld: taxWithheldEstimate,
    estimatedTaxDue: null,
    taxProfile,
    taxStatus: taxProfile === "us_personal_no_local_tax"
      ? "not_calculated_user_specific"
      : missingCostBasis ? "pending_complete_cost_basis_and_brl_fx" : "pending_brl_fx_and_annual_tax_reconciliation",
    assumptions: taxProfile === "us_personal_no_local_tax"
      ? ["US federal/state tax varies by taxpayer; not estimated", "Broker tax forms, dividends, and wash-sale adjustments are not included"]
      : ["Brazil foreign-investment tax requires complete tax lots and event-date BRL conversion; this run reports USD gains only"],
  };
}

export function parseCookies(header) {
  return Object.fromEntries(String(header || "").split(";").map(part => {
    const index = part.indexOf("=");
    return index < 0 ? [part.trim(), ""] : [part.slice(0, index).trim(), part.slice(index + 1).trim()];
  }).filter(([key]) => key));
}
