from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path

from .calendar import is_completed_market_session
from .config import StrategyConfig
from .data import load_daily_history
from .forecast import KronosEngine
from .paper import (
    apply_corporate_actions,
    infer_last_market_date,
    load_state,
    performance_metrics,
    rebalance,
    save_state,
    write_state,
)
from .portfolio import choose_targets
from .portfolio import Target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--report", default="data/latest.json")
    args = parser.parse_args()

    cfg = StrategyConfig()
    state = load_state(args.state, cfg.initial_cash)
    benchmark_history = load_daily_history(cfg.benchmark_symbol, cfg.lookback)
    market_date = benchmark_history.index[-1].date().isoformat()
    if not is_completed_market_session(market_date):
        raise RuntimeError(
            f"latest daily candle for {market_date} is still in progress; "
            "retry after 16:15 America/New_York"
        )

    migrated = False
    if state.last_market_date is None:
        inferred = infer_last_market_date(state, benchmark_history)
        if inferred:
            state.last_market_date = inferred
            migrated = True

    if state.last_market_date == market_date:
        if migrated:
            write_state(state, args.state)
            report_path = Path(args.report)
            if report_path.exists():
                legacy_report = json.loads(report_path.read_text(encoding="utf-8"))
                legacy_report["market_session"] = market_date
                legacy_report["execution_model"] = "legacy_same_close_before_v2_migration"
                legacy_report["schema_version"] = 2
                report_path.write_text(
                    json.dumps(legacy_report, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
        print(json.dumps({
            "status": "skipped",
            "reason": "market session already processed",
            "market_date": market_date,
        }, indent=2))
        return 0

    forecasts = []
    close_prices: dict[str, float] = {}
    open_prices: dict[str, float] = {}
    histories = {cfg.benchmark_symbol: benchmark_history}
    errors: dict[str, str] = {}

    symbols_to_load = list(dict.fromkeys(
        [*cfg.symbols, *state.positions, *(row.get("symbol", "") for row in state.pending_targets)]
    ))
    for symbol in symbols_to_load:
        if not symbol:
            continue
        try:
            history = histories[symbol] if symbol in histories else load_daily_history(symbol, cfg.lookback)
            symbol_date = history.index[-1].date().isoformat()
            if symbol_date != market_date:
                raise RuntimeError(f"stale session {symbol_date}; expected {market_date}")
            histories[symbol] = history
            close_prices[symbol] = float(history["close"].iloc[-1])
            open_prices[symbol] = float(history["open"].iloc[-1])
        except Exception as exc:
            errors[symbol] = f"{type(exc).__name__}: {exc}"

    forecast_history_count = sum(symbol in histories for symbol in cfg.symbols)
    if forecast_history_count < max(3, cfg.max_positions):
        raise RuntimeError(f"Too few current market histories: {forecast_history_count}; errors={errors}")

    engine = KronosEngine(cfg)
    for symbol in cfg.symbols:
        history = histories.get(symbol)
        if history is None:
            continue
        if symbol in errors:
            continue
        try:
            forecasts.append(engine.predict(symbol, history))
        except Exception as exc:
            errors[symbol] = f"{type(exc).__name__}: {exc}"

    if len(forecasts) < max(3, cfg.max_positions):
        raise RuntimeError(f"Too few successful forecasts: {len(forecasts)}; errors={errors}")

    targets = choose_targets(forecasts, cfg)
    actions = apply_corporate_actions(state, histories, state.last_market_date, market_date)

    pending_targets = [Target(**row) for row in state.pending_targets]
    pending_date = state.pending_signal_date
    warnings: list[str] = []
    trades: list[dict] = []
    if pending_targets and pending_date:
        signal_age = (date.fromisoformat(market_date) - date.fromisoformat(pending_date)).days
        if signal_age <= 0:
            warnings.append("pending signal was not older than the execution session")
        elif signal_age > cfg.max_signal_age_calendar_days:
            warnings.append(f"expired pending signal from {pending_date} was not executed")
        else:
            trades = rebalance(
                state, pending_targets, open_prices, cfg,
                signal_date=pending_date,
                execution_date=market_date,
                price_source="session_open",
            )

    state.pending_targets = [target.__dict__ for target in targets]
    state.pending_signal_date = market_date
    benchmark_session_return = (
        float(benchmark_history["close"].iloc[-1]) / float(benchmark_history["close"].iloc[-2]) - 1.0
    )
    nav = save_state(
        state, args.state, close_prices, cfg,
        market_date=market_date,
        benchmark_session_return=benchmark_session_return,
    )
    metrics = performance_metrics(state, cfg, close_prices)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_session": market_date,
        "mode": "paper",
        "execution_model": "signal_at_close_execute_next_session_open",
        "model": cfg.model_name,
        "model_revision": cfg.model_revision,
        "tokenizer_revision": cfg.tokenizer_revision,
        "nav": nav,
        "cash": state.cash,
        "positions": state.positions,
        "metrics": metrics,
        "targets": [target.__dict__ for target in targets],
        "pending_execution": {
            "signal_date": market_date,
            "target_count": len(targets),
            "expected_execution": "next_market_session_open",
        },
        "forecasts": [f.__dict__ for f in sorted(forecasts, key=lambda x: x.expected_return, reverse=True)],
        "trades": trades,
        "corporate_actions": actions,
        "warnings": warnings,
        "errors": errors,
        "schema_version": 2,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"nav": nav, "metrics": metrics, "targets": report["targets"], "trades": trades, "errors": errors}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
