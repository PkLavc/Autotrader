from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .config import StrategyConfig
from .data import load_daily_history
from .forecast import KronosEngine
from .paper import load_state, performance_metrics, rebalance, save_state
from .portfolio import choose_targets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--report", default="data/latest.json")
    args = parser.parse_args()

    cfg = StrategyConfig()
    engine = KronosEngine(cfg)
    forecasts = []
    prices: dict[str, float] = {}
    errors: dict[str, str] = {}

    for symbol in cfg.symbols:
        try:
            history = load_daily_history(symbol, cfg.lookback)
            prices[symbol] = float(history["close"].iloc[-1])
            forecasts.append(engine.predict(symbol, history))
        except Exception as exc:
            errors[symbol] = f"{type(exc).__name__}: {exc}"

    if len(forecasts) < max(3, cfg.max_positions):
        raise RuntimeError(f"Too few successful forecasts: {len(forecasts)}; errors={errors}")

    targets = choose_targets(forecasts, cfg)
    state = load_state(args.state, cfg.initial_cash)
    trades = rebalance(state, targets, prices, cfg)
    nav = save_state(state, args.state, prices, cfg)
    metrics = performance_metrics(state, cfg, prices)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "paper",
        "model": cfg.model_name,
        "model_revision": cfg.model_revision,
        "tokenizer_revision": cfg.tokenizer_revision,
        "nav": nav,
        "cash": state.cash,
        "positions": state.positions,
        "metrics": metrics,
        "targets": [target.__dict__ for target in targets],
        "forecasts": [f.__dict__ for f in sorted(forecasts, key=lambda x: x.expected_return, reverse=True)],
        "trades": trades,
        "errors": errors,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"nav": nav, "metrics": metrics, "targets": report["targets"], "trades": trades, "errors": errors}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
