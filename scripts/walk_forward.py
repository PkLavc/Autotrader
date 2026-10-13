from __future__ import annotations

import argparse
import json
from pathlib import Path

from autotrader.backtest import load_jsonl, walk_forward
from autotrader.config import StrategyConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay point-in-time Kronos forecasts")
    parser.add_argument("input", help="JSONL sessions with prices and forecasts")
    parser.add_argument("--output", default="data/backtest-latest.json")
    args = parser.parse_args()

    result = walk_forward(load_jsonl(args.input), StrategyConfig())
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result["metrics"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
