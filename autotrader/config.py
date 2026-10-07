from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ref(filename: str, env_name: str) -> str | None:
    override = os.getenv(env_name)
    if override:
        return override.strip()
    path = ROOT / filename
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    return None


@dataclass(frozen=True)
class StrategyConfig:
    model_name: str = "NeoQuasar/Kronos-base"
    tokenizer_name: str = "NeoQuasar/Kronos-Tokenizer-base"
    model_revision: str | None = field(default_factory=lambda: _ref("MODEL_REF", "AUTOTRADER_MODEL_REVISION"))
    tokenizer_revision: str | None = field(default_factory=lambda: _ref("TOKENIZER_REF", "AUTOTRADER_TOKENIZER_REVISION"))
    lookback: int = 400
    prediction_days: int = 5
    sample_count: int = 3
    top_p: float = 0.9
    temperature: float = 1.0
    initial_cash: float = 10_000.0
    max_positions: int = 5
    max_weight_per_position: float = 0.18
    cash_buffer: float = 0.10
    min_forecast_return: float = 0.01
    rebalance_threshold: float = 0.03
    slippage_bps: float = 10.0
    benchmark_symbol: str = "SPY"
    symbols: tuple[str, ...] = field(default_factory=lambda: (
        "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
        "META", "AVGO", "JPM", "V", "MA", "LLY", "COST", "WMT",
        "XOM", "UNH", "HD", "NFLX",
    ))
