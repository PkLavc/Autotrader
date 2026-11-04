"""Offline five-day league replay from point-in-time signal/price records.

Input must contain as-of signals and contemporaneous spot prices. This tool
cannot backfill Kronos forecasts from future prices. Results are illustrative
and do not certify real devnet execution or competition eligibility.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from .league_strategy import LeaguePolicy, _utc, decide


def replay(records: list[dict[str, Any]], *, start_utc: str,
           end_utc: str, eligible_symbols: list[str],
           initial_usdc: float = 1000.0, fee_bps: float = 40.0,
           slippage_bps: float = 20.0,
           policy: LeaguePolicy | None = None) -> dict[str, Any]:
    p = policy or LeaguePolicy()
    start, end = _utc(start_utc), _utc(end_utc)
    if not start < end or not records:
        raise ValueError("invalid season/replay period")
    if not math.isfinite(initial_usdc) or initial_usdc <= 0:
        raise ValueError("invalid initial USDC")
    if not (0 <= fee_bps < 10000 and 0 <= slippage_bps < 10000):
        raise ValueError("invalid trading costs")
    if len(records) < 3:
        raise ValueError("insufficient replay observations")
    fee, slip = fee_bps / 10000.0, slippage_bps / 10000.0
    cash = float(initial_usdc)
    holdings: dict[str, dict] = {}
    peak_equity = cash
    max_drawdown = 0.0
    events: list[dict] = []
    curve = []
    previous_time: datetime | None = None

    for i, record in enumerate(records):
        now = _utc(record.get("timestamp"))
        if now < start or now > end or (previous_time and now <= previous_time):
            raise ValueError("record timestamps must be increasing within season")
        previous_time = now
        quotes = record.get("prices_usdc")
        if not isinstance(quotes, dict):
            raise ValueError("missing point-in-time spot prices")
        prices = {}
        for symbol, value in quotes.items():
            price = float(value)
            if not math.isfinite(price) or price <= 0:
                raise ValueError("invalid spot price")
            prices[symbol] = price
        if any(symbol not in prices for symbol in holdings):
            raise ValueError("missing held-token price (cannot mark to market)")
        for symbol, position in holdings.items():
            position["peak_price_usdc"] = max(position["peak_price_usdc"], prices[symbol])
        equity = cash + sum(position["qty"] * prices[symbol]
                            for symbol, position in holdings.items())
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, 1-equity/peak_equity)
        positions = {
            symbol: {
                "value_usdc": position["qty"] * prices[symbol],
                "price_usdc": prices[symbol],
                "entry_price_usdc": position["entry_price_usdc"],
                "peak_price_usdc": position["peak_price_usdc"],
            }
            for symbol, position in holdings.items()
        }
        context = {
            "verified": True, "restrictions_verified": True,
            "category": "crypto-bro", "wallet_ready": True,
            "network": "solana-devnet", "season_status": "active",
            "start_utc": start_utc, "end_utc": end_utc,
            "start_snapshot_confirmed": True,
            "eligible_symbols": eligible_symbols,
            "max_position_weight": p.max_position_weight,
            "max_total_exposure": p.max_total_exposure,
        }
        report = record.get("report", {})
        # At official snapshot boundaries, valuation comes BEFORE any trades.
        # There cannot be a trade at the end snapshot.
        if now == start or now == end:
            decision = {"status": "SNAPSHOT", "actions": []}
        else:
            decision = decide(context, {
                "equity_usdc": equity, "cash_usdc": cash,
                "peak_equity_usdc": peak_equity, "positions": positions,
            }, report, now=now, policy=p)

        for action in decision.get("actions", []):
            symbol = action["symbol"]
            if symbol not in prices:
                raise ValueError("missing traded-token spot quote")
            if action["side"] == "SELL_TO_USDC" and symbol in holdings:
                h = holdings.pop(symbol)
                gross = h["qty"] * prices[symbol]
                received = gross * (1-fee) * (1-slip)
                cash += received
                events.append({"timestamp": now.isoformat(), "side": "SELL",
                               "symbol": symbol, "proceeds_usdc": round(received, 6)})
            elif action["side"] == "BUY_WITH_USDC" and symbol not in holdings:
                input_usdc = min(cash, float(action["maximum_input_usdc"]))
                if input_usdc < p.min_order_usdc:
                    continue
                fill_price = prices[symbol] * (1+slip)
                units = input_usdc*(1-fee) / fill_price
                cash -= input_usdc
                holdings[symbol] = {
                    "qty": units, "entry_price_usdc": fill_price,
                    "peak_price_usdc": fill_price,
                }
                events.append({"timestamp": now.isoformat(), "side": "BUY",
                               "symbol": symbol, "input_usdc": round(input_usdc, 6)})
        marked = cash + sum(h["qty"]*prices[sym] for sym, h in holdings.items())
        peak_equity = max(peak_equity, marked)
        max_drawdown = max(max_drawdown, 1-marked/peak_equity)
        curve.append({
            "timestamp": now.isoformat(), "equity_usdc": round(marked, 6),
            "cash_usdc": round(cash, 6), "held_symbols": sorted(holdings),
            "decision_status": decision["status"],
        })

    covers_snapshots = (_utc(records[0]["timestamp"]) == start and
                        _utc(records[-1]["timestamp"]) == end)
    usdc_only_at_end = not holdings
    final_equity = curve[-1]["equity_usdc"]
    return {
        "mode": "offline_point_in_time_replay",
        "executionAuthorized": False,
        "official_result": False,
        "snapshot_coverage": covers_snapshots,
        "end_snapshot_usdc_only": usdc_only_at_end,
        "simulation_eligible": covers_snapshots and usdc_only_at_end,
        "initial_usdc": initial_usdc,
        "final_usdc_equivalent": final_equity,
        "net_return_pct": round((final_equity / initial_usdc-1)*100, 4),
        "max_drawdown_pct": round(max_drawdown*100, 4),
        "trade_count": len(events),
        "fees_bps_per_side": fee_bps,
        "slippage_bps_per_side": slippage_bps,
        "events": events,
        "equity_curve": curve,
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Point-in-time AudaceFi season replay")
    parser.add_argument("records", help="JSON list of timestamp/prices_usdc/report records")
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--end-utc", required=True)
    parser.add_argument("--eligible", nargs="+", required=True)
    args = parser.parse_args()
    try:
        records = json.loads(Path(args.records).read_text(encoding="utf-8"))
        print(json.dumps(replay(
            records, start_utc=args.start_utc, end_utc=args.end_utc,
            eligible_symbols=args.eligible,
        ), indent=2, sort_keys=True))
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        print(json.dumps({"status": "REPLAY_INPUT_INVALID", "official_result": False}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
