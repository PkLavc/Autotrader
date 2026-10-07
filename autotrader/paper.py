from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from .config import StrategyConfig
from .portfolio import Target


@dataclass
class PaperState:
    cash: float
    positions: dict[str, float]
    nav_history: list[dict]
    trades: list[dict]
    last_prices: dict[str, float]
    benchmark_start_price: float | None = None


def load_state(path: str, initial_cash: float) -> PaperState:
    p = Path(path)
    if not p.exists():
        return PaperState(initial_cash, {}, [], [], {})
    raw = json.loads(p.read_text(encoding="utf-8"))
    return PaperState(
        cash=float(raw["cash"]),
        positions={k: float(v) for k, v in raw.get("positions", {}).items()},
        nav_history=list(raw.get("nav_history", [])),
        trades=list(raw.get("trades", [])),
        last_prices={k: float(v) for k, v in raw.get("last_prices", {}).items()},
        benchmark_start_price=(
            float(raw["benchmark_start_price"])
            if raw.get("benchmark_start_price") is not None
            else None
        ),
    )


def _effective_price(state: PaperState, symbol: str, prices: dict[str, float]) -> float:
    if symbol in prices:
        return float(prices[symbol])
    if symbol in state.last_prices:
        return float(state.last_prices[symbol])
    raise RuntimeError(f"No current or prior price available for held symbol {symbol}")


def mark_to_market(state: PaperState, prices: dict[str, float]) -> float:
    return state.cash + sum(
        qty * _effective_price(state, sym, prices)
        for sym, qty in state.positions.items()
    )


def rebalance(state: PaperState, targets: list[Target], prices: dict[str, float], cfg: StrategyConfig) -> list[dict]:
    nav = mark_to_market(state, prices)
    target_weights = {t.symbol: t.weight for t in targets}
    all_symbols = set(state.positions) | set(target_weights)
    trades: list[dict] = []
    slip = cfg.slippage_bps / 10_000.0

    desired_values = {sym: nav * target_weights.get(sym, 0.0) for sym in all_symbols}
    current_values = {
        sym: state.positions.get(sym, 0.0) * _effective_price(state, sym, prices)
        for sym in all_symbols
        if sym in state.positions
    }

    for sym in sorted(all_symbols):
        if sym not in prices:
            continue
        delta = desired_values.get(sym, 0.0) - current_values.get(sym, 0.0)
        if delta >= 0:
            continue
        if nav and abs(delta) / nav < cfg.rebalance_threshold:
            continue
        px = prices[sym] * (1.0 - slip)
        qty = min(state.positions.get(sym, 0.0), abs(delta) / px)
        if qty <= 0:
            continue
        state.positions[sym] = state.positions.get(sym, 0.0) - qty
        if state.positions[sym] <= 1e-12:
            state.positions.pop(sym, None)
        state.cash += qty * px
        trades.append({"side": "SELL", "symbol": sym, "qty": qty, "price": px})

    for sym in sorted(target_weights, key=lambda s: target_weights[s], reverse=True):
        if sym not in prices:
            continue
        current = state.positions.get(sym, 0.0) * prices[sym]
        delta = desired_values[sym] - current
        if delta <= 0:
            continue
        if nav and abs(delta) / nav < cfg.rebalance_threshold:
            continue
        px = prices[sym] * (1.0 + slip)
        spend = min(delta, state.cash)
        qty = spend / px
        if qty <= 0:
            continue
        state.positions[sym] = state.positions.get(sym, 0.0) + qty
        state.cash -= qty * px
        trades.append({"side": "BUY", "symbol": sym, "qty": qty, "price": px})

    if state.cash < -1e-7:
        raise RuntimeError(f"Paper account overspent cash: {state.cash}")

    now = datetime.now(timezone.utc).isoformat()
    for trade in trades:
        trade["timestamp"] = now
        state.trades.append(trade)
    return trades


def save_state(state: PaperState, path: str, prices: dict[str, float], cfg: StrategyConfig) -> float:
    state.last_prices.update({k: float(v) for k, v in prices.items() if math.isfinite(float(v))})
    if state.benchmark_start_price is None and cfg.benchmark_symbol in prices:
        state.benchmark_start_price = float(prices[cfg.benchmark_symbol])

    nav = mark_to_market(state, prices)
    now = datetime.now(timezone.utc).isoformat()
    state.nav_history.append({"timestamp": now, "nav": nav, "cash": state.cash})
    payload = {
        "cash": state.cash,
        "positions": state.positions,
        "last_prices": state.last_prices,
        "benchmark_start_price": state.benchmark_start_price,
        "nav_history": state.nav_history[-1500:],
        "trades": state.trades[-5000:],
        "updated_at": now,
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return nav


def performance_metrics(state: PaperState, cfg: StrategyConfig, prices: dict[str, float]) -> dict:
    navs = [float(row["nav"]) for row in state.nav_history if float(row.get("nav", 0)) > 0]
    current_nav = navs[-1] if navs else cfg.initial_cash
    peak = 0.0
    max_drawdown = 0.0
    for nav in navs:
        peak = max(peak, nav)
        if peak > 0:
            max_drawdown = min(max_drawdown, (nav / peak) - 1.0)

    benchmark_return = None
    benchmark_now = prices.get(cfg.benchmark_symbol, state.last_prices.get(cfg.benchmark_symbol))
    if state.benchmark_start_price and benchmark_now:
        benchmark_return = (float(benchmark_now) / state.benchmark_start_price) - 1.0

    return {
        "initial_cash": cfg.initial_cash,
        "current_nav": current_nav,
        "total_return": (current_nav / cfg.initial_cash) - 1.0,
        "max_drawdown": max_drawdown,
        "benchmark_symbol": cfg.benchmark_symbol,
        "benchmark_return": benchmark_return,
        "trade_count": len(state.trades),
        "observation_count": len(state.nav_history),
    }
