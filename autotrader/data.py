from __future__ import annotations

import time
import pandas as pd
import yfinance as yf

REQUIRED = ["open", "high", "low", "close", "volume"]


def load_daily_history(symbol: str, lookback: int, attempts: int = 3) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            df = yf.download(
                symbol,
                period="3y",
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
                timeout=30,
            )
            if df.empty:
                raise RuntimeError(f"No market data returned for {symbol}")

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.rename(columns={c: c.lower() for c in df.columns})
            missing = [c for c in REQUIRED if c not in df.columns]
            if missing:
                raise RuntimeError(f"{symbol}: missing columns {missing}")

            out = df[REQUIRED].dropna().copy()
            out["amount"] = out["close"] * out["volume"]
            out.index = pd.to_datetime(out.index).tz_localize(None)

            if len(out) < lookback:
                raise RuntimeError(f"{symbol}: only {len(out)} rows, need {lookback}")
            return out.tail(lookback)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"{symbol}: market-data fetch failed after {attempts} attempts: {last_error}")
