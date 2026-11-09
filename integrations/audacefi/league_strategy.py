"""AudaceFi Crypto Bro season decision engine (read-only, no order submission).

Accepts a verified, normalized season/account context and independent crypto
signals. It never invents token eligibility, season dates or account balances.
Winning is not guaranteed: the league ranks net USDC returns, not model scores.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LeaguePolicy:
    max_position_weight: float = 0.35
    max_total_exposure: float = 0.70
    risk_budget_per_trade: float = 0.0125
    maximum_season_drawdown: float = 0.15
    stop_distance_min: float = 0.07
    stop_distance_max: float = 0.18
    closing_buffer_hours: float = 8.0
    new_entry_cutoff_hours: float = 16.0
    maximum_signal_age_hours: float = 6.0
    round_trip_cost_bps: float = 80.0
    minimum_net_edge: float = 0.025
    min_order_usdc: float = 1.0
    max_assets: int = 3


def _utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp_missing")
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if when.tzinfo is None:
        raise ValueError("timezone_required")
    return when.astimezone(timezone.utc)


def _positive(value: Any, name: str, *, zero: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError("invalid_" + name)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_" + name) from exc
    if not math.isfinite(number) or number < 0 or (not zero and number == 0):
        raise ValueError("invalid_" + name)
    return number


def _hold(reason: str, *, phase: str = "blocked", **details: Any) -> dict[str, Any]:
    return {
        "status": "HOLD", "reason": reason, "phase": phase, "actions": [],
        "executionAuthorized": False, **details,
    }


def _sell_all(positions: dict, reason: str, *, phase: str) -> dict:
    actions = [
        {"side": "SELL_TO_USDC", "symbol": symbol,
         "estimated_value_usdc": round(float(position["value_usdc"]), 6),
         "reason": reason}
        for symbol, position in sorted(positions.items())
        if float(position["value_usdc"]) > 0
    ]
    if not actions:
        return _hold("USDC_ONLY_READY", phase=phase)
    return {
        "status": "EXIT_REQUIRED", "reason": reason, "phase": phase,
        "actions": actions, "executionAuthorized": False,
        "requires_wallet_confirmation": True,
    }


def decide(context: dict, portfolio: dict, report: dict, *,
           now: datetime | None = None,
           policy: LeaguePolicy | None = None) -> dict[str, Any]:
    """Propose league-compatible spot actions; nothing is signed or traded.

    context is a normalized, verified representation of the official account
    API. Do not pass scraped, speculative or inferred season settings.
    Portfolio/account snapshots must be current and independently reconciled.
    """
    p = policy or LeaguePolicy()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now_timezone_required")
    now = now.astimezone(timezone.utc)
    if not isinstance(context, dict) or not isinstance(portfolio, dict):
        return _hold("VERIFIED_ACCOUNT_CONTEXT_REQUIRED")
    if context.get("verified") is not True or context.get("category") != "crypto-bro":
        return _hold("VERIFIED_CRYPTO_BRO_CONTEXT_REQUIRED")
    if context.get("restrictions_verified") is not True:
        return _hold("OFFICIAL_TOKEN_AND_POSITION_LIMITS_REQUIRED")
    if context.get("wallet_ready") is not True:
        return _hold("SEASON_WALLET_NOT_READY")
    if context.get("network") != "solana-devnet":
        return _hold("DEVNET_ONLY")
    if context.get("season_status") not in {"active", "scheduled", "closed"}:
        return _hold("SEASON_STATE_UNVERIFIED")
    try:
        start = _utc(context.get("start_utc"))
        end = _utc(context.get("end_utc"))
        if start >= end or (end-start).total_seconds() > 10*86400:
            return _hold("SEASON_TIME_INVALID")
        eligible = context.get("eligible_symbols")
        if not isinstance(eligible, list) or not eligible or len(eligible) > 30 or any(
            not isinstance(symbol, str) or
            re.fullmatch(r"[A-Z][A-Z0-9]{1,11}", symbol) is None
            for symbol in eligible
        ):
            return _hold("OFFICIAL_ALLOWLIST_NOT_SUPPORTED")
        if len(set(eligible)) != len(eligible):
            return _hold("DUPLICATE_ELIGIBLE_TOKEN")
        cap = min(p.max_position_weight, _positive(context.get("max_position_weight"), "category_position_cap"))
        total_cap = min(p.max_total_exposure, _positive(context.get("max_total_exposure"), "category_exposure_cap"))
        equity = _positive(portfolio.get("equity_usdc"), "equity")
        cash = _positive(portfolio.get("cash_usdc"), "cash", zero=True)
        peak = _positive(portfolio.get("peak_equity_usdc"), "peak_equity")
        positions = portfolio.get("positions")
        if not isinstance(positions, dict):
            return _hold("POSITION_SNAPSHOT_REQUIRED")
        if not all(isinstance(symbol, str) and isinstance(position, dict)
                   for symbol, position in positions.items()):
            return _hold("POSITION_SNAPSHOT_INVALID")
        for symbol, position in positions.items():
            if symbol not in eligible:
                return _hold("UNAUTHORIZED_HELD_TOKEN", symbol=symbol)
            for field in ("value_usdc", "price_usdc", "entry_price_usdc", "peak_price_usdc"):
                _positive(position.get(field), field, zero=(field == "value_usdc"))
        exposed = sum(float(pos["value_usdc"]) for pos in positions.values())
        if abs(cash + exposed - equity) > max(1.0, .01*equity):
            return _hold("PORTFOLIO_RECONCILIATION_FAILED")
    except (ValueError, OverflowError) as exc:
        return _hold("INVALID_ACCOUNT_INPUT", detail=str(exc))

    if now < start or context["season_status"] == "scheduled":
        return _sell_all(positions, "START_SNAPSHOT_MUST_BE_USDC_ONLY", phase="preseason")
    if now >= end or context["season_status"] == "closed":
        return _sell_all(positions, "FINAL_SNAPSHOT_MUST_BE_USDC_ONLY", phase="ended")
    if context.get("start_snapshot_confirmed") is not True:
        return _hold("START_SNAPSHOT_NOT_CONFIRMED", phase="active")
    remaining_hours = (end-now).total_seconds()/3600.0
    if remaining_hours <= p.closing_buffer_hours:
        return _sell_all(positions, "FINAL_SNAPSHOT_USDC_EXIT", phase="final_window")

    drawdown = max(0.0, 1.0-equity/peak)
    if drawdown >= p.maximum_season_drawdown:
        return _sell_all(positions, "SEASON_DRAWDOWN_LIMIT", phase="defensive_exit")

    # Risk exits are evaluated from reconciled portfolio prices, not Kronos.
    exits: list[dict] = []
    for symbol, position in sorted(positions.items()):
        entry, high = float(position["entry_price_usdc"]), float(position["peak_price_usdc"])
        price = float(position["price_usdc"])
        trailing = .025 if isinstance(report, dict) and report.get("model") == "coinbase_completed_15m" else .10
        stop = max(entry*(1-p.stop_distance_max), high*(1-trailing))
        if price <= stop and float(position["value_usdc"]) > 0:
            exits.append({
                "side": "SELL_TO_USDC", "symbol": symbol,
                "estimated_value_usdc": round(float(position["value_usdc"]), 6),
                "reason": "POSITION_PROTECTIVE_EXIT",
                "stop_reference_usdc": round(stop, 8),
            })

    # Protective exits never depend on the availability of the forecast model.
    if exits:
        return {
            "status": "EXIT_REQUIRED", "reason": "POSITION_PROTECTIVE_EXIT",
            "phase": "active", "actions": exits, "executionAuthorized": False,
            "requires_wallet_confirmation": True,
        }

    # A stale model must never cause new exposure.
    if not isinstance(report, dict) or report.get("asset_class") != "crypto":
        return _hold("CRYPTO_SIGNALS_REQUIRED", phase="active")
    try:
        age = (now-_utc(report.get("generated_at"))).total_seconds()/3600
    except ValueError:
        return _hold("SIGNAL_TIMESTAMP_INVALID", phase="active")
    if not 0 <= age <= p.maximum_signal_age_hours:
        return _hold("SIGNALS_STALE", phase="active")
    signals = report.get("signals")
    if not isinstance(signals, list) or len(signals) > 20:
        return _hold("SIGNAL_FORMAT_INVALID", phase="active")
    if report.get("model") == "coinbase_completed_15m" and positions:
        for symbol in positions:
            signal = next((row for row in signals if isinstance(row, dict) and row.get("symbol") == symbol), None)
            if signal is not None:
                try:
                    if float(signal["momentum_1h"]) < -.003 and float(signal["momentum_3h"]) < -.005:
                        return _sell_all(positions, "INTRADAY_TREND_REVERSAL", phase="defensive_exit")
                except (KeyError, TypeError, ValueError):
                    pass
    if remaining_hours <= p.new_entry_cutoff_hours:
        return _hold("NEW_ENTRIES_CLOSED_BEFORE_SNAPSHOT", phase="closing", remaining_hours=round(remaining_hours, 2))

    candidates = []
    intraday = report.get("model") == "coinbase_completed_15m"
    for signal in signals:
        if not isinstance(signal, dict) or signal.get("symbol") not in eligible:
            continue
        symbol = signal["symbol"]
        if intraday:
            try:
                moves = [float(signal[name]) for name in (
                    "momentum_1h", "momentum_3h", "momentum_6h",
                    "momentum_12h", "momentum_24h")]
                volume = _positive(signal.get("median_15m_dollar_volume_usd"), "liquidity")
                _positive(signal.get("last_close_usd"), "reference_price")
            except (KeyError, TypeError, ValueError):
                continue
            if not all(math.isfinite(move) for move in moves):
                continue
            m1, m3, m6, m12, m24 = moves
            if (m1 <= 0 or m3 < .003 or m6 < .007 or m12 < -.005 or
                    m24 < -.02 or volume < 100_000 or signal.get("above_ema20") is not True):
                continue
            strength = .25*m3 + .40*m6 + .25*m12 + .10*m24
            if strength < p.minimum_net_edge + p.round_trip_cost_bps/10_000:
                continue
            stop_distance = p.stop_distance_max
            weight = min(cap, p.risk_budget_per_trade/stop_distance)
            candidates.append((strength, symbol, weight, strength, stop_distance))
            continue
        try:
            expected = _positive(signal.get("expected_return"), "expected_return", zero=True)
            momentum3 = float(signal["momentum_3d"])
            momentum7 = float(signal["momentum_7d"])
            vol = _positive(signal.get("volatility_annualized"), "annualized_volatility")
            liquidity = _positive(signal.get("avg_dollar_volume_usd"), "liquidity")
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(x) for x in [momentum3, momentum7, vol]):
            continue
        if momentum7 <= 0 or momentum3 < -.01 or vol > 2.4 or liquidity < 10_000_000:
            continue
        # Discount the uncalibrated AI forecast; reward confirmed momentum
        # and require enough predicted return to cover stressed round-trip costs.
        edge = .45*min(expected, .25) + .35*momentum7 + .20*momentum3
        net_edge = edge-p.round_trip_cost_bps/10_000.0
        if net_edge < p.minimum_net_edge:
            continue
        stop_distance = max(p.stop_distance_min, min(p.stop_distance_max, 2*vol/math.sqrt(365)))
        weight = min(cap, p.risk_budget_per_trade/stop_distance)
        score = net_edge/max(vol*math.sqrt(5/365), .025)
        candidates.append((score, symbol, weight, net_edge, stop_distance))

    candidates.sort(key=lambda row: (-row[0], row[1]))
    if not candidates:
        return _hold("NO_POSITIVE_NET_EDGE", phase="active", drawdown=round(drawdown, 6))
    actions = []
    free_exposure = max(0.0, min(total_cap*equity-exposed, cash))
    held_count = len([v for v in positions.values() if float(v["value_usdc"]) > 0])
    for score, symbol, weight, edge, stop in candidates:
        if symbol in positions or held_count+len(actions) >= p.max_assets:
            continue
        notional = min(weight*equity, free_exposure)
        if notional < p.min_order_usdc:
            continue
        action = {
            "side": "BUY_WITH_USDC", "symbol": symbol,
            "maximum_input_usdc": round(notional, 6),
            "stop_reference_fraction": round(stop, 6),
            "ranking_score": round(score, 6),
        }
        action["trend_strength" if intraday else "estimated_net_edge"] = round(edge, 6)
        if intraday:
            action["reference_price_usd"] = float(next(
                row["last_close_usd"] for row in signals
                if isinstance(row, dict) and row.get("symbol") == symbol))
        actions.append(action)
        free_exposure -= notional
    if not actions:
        return _hold("ALLOCATION_LIMIT_OR_ALREADY_HELD", phase="active", drawdown=round(drawdown, 6))
    return {
        "status": "PROPOSED", "phase": "active", "actions": actions,
        "remaining_hours": round(remaining_hours, 2),
        "drawdown": round(drawdown, 6),
        "executionAuthorized": False,
        "requires_wallet_confirmation": True,
        "model": "league_crypto_bro_intraday_v1" if intraday else "league_crypto_bro_v1",
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Read-only AudaceFi league strategy decision")
    parser.add_argument("--context", required=True, help="Verified normalized season context JSON")
    parser.add_argument("--portfolio", required=True, help="Reconciled wallet/portfolio JSON")
    parser.add_argument("--signals", required=True, help="Crypto-only forecast report")
    args = parser.parse_args()
    try:
        context = json.loads(Path(args.context).read_text(encoding="utf-8"))
        portfolio = json.loads(Path(args.portfolio).read_text(encoding="utf-8"))
        signals = json.loads(Path(args.signals).read_text(encoding="utf-8"))
        outcome = decide(context, portfolio, signals)
        print(json.dumps(outcome, indent=2, sort_keys=True))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        print(json.dumps(_hold("INPUT_UNAVAILABLE_OR_INVALID")))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
