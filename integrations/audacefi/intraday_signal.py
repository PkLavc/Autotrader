"""Shared completed-candle signal calculations for live decisions and research."""
from __future__ import annotations

import math
from statistics import median


def signal_from_rows(symbol: str, rows: list[tuple[int, float, float]],
                     index: int) -> dict | None:
    """Build the exact live 15m signal from a normalized candle sequence."""
    if (not isinstance(symbol, str) or not symbol.isascii() or
            not symbol.isalnum() or index < 96 or index >= len(rows)):
        return None
    close = float(rows[index][1])
    moves = {f"momentum_{label}": close / float(rows[index - periods][1]) - 1
             for label, periods in (("1h", 4), ("3h", 12), ("6h", 24),
                                    ("12h", 48), ("24h", 96))}
    ema = float(rows[index - 20][1])
    for cursor in range(index - 19, index + 1):
        ema = (2 / 21) * float(rows[cursor][1]) + (19 / 21) * ema
    volume = median(float(price) * float(amount)
                    for _, price, amount in rows[index - 95:index + 1])
    if not all(math.isfinite(value) for value in (close, ema, volume, *moves.values())):
        return None
    return {"symbol": symbol, "last_close_usd": close,
            "median_15m_dollar_volume_usd": volume,
            "above_ema20": close > ema, **moves}
