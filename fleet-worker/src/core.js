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

export function planRebalance({ equity, cash, positions, targets, signalDate, limits: rawLimits }) {
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
    orders.push({
      symbol, side: "sell", qty, estimatedNotional,
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
    const notional = Math.floor(Math.min(delta, limits.maxOrderNotional, remaining, availableCash) * 100) / 100;
    if (notional < 1) continue;
    orders.push({
      symbol, side: "buy", notional, estimatedNotional: notional,
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
  if (order.qty != null) payload.qty = Number(order.qty).toFixed(6).replace(/\.?0+$/, "");
  if (order.notional != null) payload.notional = Number(order.notional).toFixed(2);
  return payload;
}

export function parseCookies(header) {
  return Object.fromEntries(String(header || "").split(";").map(part => {
    const index = part.indexOf("=");
    return index < 0 ? [part.trim(), ""] : [part.slice(0, index).trim(), part.slice(index + 1).trim()];
  }).filter(([key]) => key));
}
