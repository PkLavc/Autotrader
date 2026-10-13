from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Iterable

from .config import StrategyConfig
from .forecast import Forecast
from .paper import PaperState, mark_to_market, performance_metrics, rebalance
from .portfolio import Target, choose_targets


def load_jsonl(path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on line {line_number}: {exc}") from exc
    return records


def walk_forward(records: Iterable[dict[str, Any]], cfg: StrategyConfig) -> dict[str, Any]:
    """Replay precomputed, point-in-time forecasts with next-session-open execution."""
    state = PaperState(cfg.initial_cash, {}, [], [], {})
    pending: list[Target] = []
    pending_date: str | None = None
    seen: set[str] = set()
    previous_date: str | None = None
    signal_history: list[dict[str, Any]] = []

    for record in records:
        market_date = str(record["market_date"])
        if market_date in seen or (previous_date is not None and market_date <= previous_date):
            raise ValueError(f"market dates must be unique and increasing: {market_date}")
        seen.add(market_date)
        previous_date = market_date

        prices = record.get("prices", {})
        open_prices = {symbol: float(row["open"]) for symbol, row in prices.items()}
        close_prices = {symbol: float(row["close"]) for symbol, row in prices.items()}
        if cfg.benchmark_symbol not in close_prices:
            raise ValueError(f"missing benchmark {cfg.benchmark_symbol} on {market_date}")

        session_trades = rebalance(
            state, pending, open_prices, cfg,
            signal_date=pending_date,
            execution_date=market_date,
            price_source="session_open",
        ) if pending else []

        state.last_prices.update(close_prices)
        if state.benchmark_start_price is None:
            state.benchmark_start_price = close_prices[cfg.benchmark_symbol]
            state.benchmark_value = 1.0
        elif state.benchmark_value is not None:
            prior_benchmark = float(state.nav_history[-1]["benchmark_close"])
            state.benchmark_value *= close_prices[cfg.benchmark_symbol] / prior_benchmark
        nav = mark_to_market(state, close_prices)
        state.last_market_date = market_date
        state.nav_history.append({
            "market_date": market_date,
            "timestamp": f"{market_date}T21:00:00+00:00",
            "nav": nav,
            "cash": state.cash,
            "benchmark_close": close_prices[cfg.benchmark_symbol],
        })

        forecasts = [Forecast(**row) for row in record.get("forecasts", [])]
        pending = choose_targets(forecasts, cfg)
        pending_date = market_date
        signal_history.append({
            "market_date": market_date,
            "targets": [asdict(target) for target in pending],
            "executed_trades": len(session_trades),
            "nav": nav,
        })

    metrics = performance_metrics(state, cfg, state.last_prices)
    return {
        "schema_version": 1,
        "execution_model": "signal_at_close_execute_next_session_open",
        "metrics": metrics,
        "equity_curve": state.nav_history,
        "trades": state.trades,
        "signals": signal_history,
        "pending_targets_not_executed": [asdict(target) for target in pending],
    }
