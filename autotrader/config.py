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
    random_seed: int = 20_261_013
    initial_cash: float = 10_000.0
    max_positions: int = 5
    max_weight_per_position: float = 0.18
    cash_buffer: float = 0.10
    min_forecast_return: float = 0.01
    rebalance_threshold: float = 0.03
    slippage_bps: float = 10.0
    # Alpaca self-directed US equities: standard commission-free schedule.
    # Regulatory rates are estimates; actual charges are account/day aggregated.
    commission_per_share: float = 0.0
    minimum_commission: float = 0.0
    maximum_commission_rate: float = 0.01
    sec_fee_rate: float = 0.0000206
    sec_fee_minimum: float = 0.0
    taf_fee_per_share: float = 0.000195
    taf_fee_cap_per_order: float = 9.79
    cat_fee_per_share: float = 0.000003
    estimated_tax_rate: float = 0.0
    max_abs_forecast_return: float = 0.30
    max_signal_age_calendar_days: int = 7
    minimum_history_for_metrics: int = 20
    benchmark_symbol: str = "SPY"
    symbols: tuple[str, ...] = field(default_factory=lambda: (
        "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
        "META", "AVGO", "JPM", "V", "MA", "LLY", "COST", "WMT",
        "XOM", "UNH", "HD", "NFLX",
    ))
