"""Conservative walk-forward intraday parameter selection for AudaceFi Devnet.

Uses completed Coinbase 15-minute candles only. Training and holdout trades
enter at the NEXT completed close; results include round-trip fees and slippage.
An unvalidated variant NEVER relaxes the existing trading rules.
No network, credentials, order placement, or model-generated executable code.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math

from .intraday_signal import signal_from_rows
from .league_strategy import LeaguePolicy, decide

# Parameter universe is fixed and small, preventing arbitrary risk changes.
PRESETS = {
    "baseline": {"intraday_m3_min": .003, "intraday_m6_min": .007,
                 "intraday_m12_min": -.005, "intraday_m24_min": -.02,
                 "minimum_net_edge": .004},
    "breakout": {"intraday_m3_min": .006, "intraday_m6_min": .012,
                 "intraday_m12_min": .004, "intraday_m24_min": -.005,
                 "minimum_net_edge": .006},
    "steady": {"intraday_m3_min": .004, "intraday_m6_min": .009,
               "intraday_m12_min": 0.0, "intraday_m24_min": -.01,
               "minimum_net_edge": .005},
    "early": {"intraday_m3_min": .001, "intraday_m6_min": .004,
              "intraday_m12_min": -.012, "intraday_m24_min": -.025,
              "minimum_net_edge": .003},
    # Research candidate only. Historical OOS approval is required before use.
    "recovery": {"intraday_m3_min": .008, "intraday_m6_min": .003,
                 "intraday_m12_min": -.06, "intraday_m24_min": -.06,
                 "minimum_net_edge": .004, "intraday_strategy": "recovery"},
}
ROUND_TRIP_COST = .003  # Current live policy estimate, all-in round trip.
FEE_PER_SWAP = .001
SLIPPAGE_PER_SWAP = .0005
SEASON_CANDLES = 5 * 24 * 4
MAX_ROLLING_SWAPS = 10
MAX_BUY_SWAPS_WITH_RESERVED_EXIT = 8
STARTING_EQUITY_USDC = 10_000.0
MIN_TRAIN_TRADES = 3
MIN_TEST_TRADES = 3
REQUIRED_TEST_EDGE = .002


def policy_for(profile: str, base: LeaguePolicy) -> LeaguePolicy:
    if profile not in PRESETS:
        return base
    return replace(base, **PRESETS[profile])


def validate_candles(rows: object) -> list[tuple[int, float, float]]:
    """Reject sparse/unfinished/duplicated bars; do not pad missing data."""
    if not isinstance(rows, list) or len(rows) < 100 or len(rows) > 50_000:
        return []
    parsed = []
    try:
        for row in rows:
            if not isinstance(row, (tuple, list)) or len(row) != 3:
                return []
            ts, price, volume = row
            if (not isinstance(ts, int) or isinstance(ts, bool) or ts % 900 or
                not isinstance(price, (int, float)) or isinstance(price, bool) or
                not isinstance(volume, (int, float)) or isinstance(volume, bool)):
                return []
            price, volume = float(price), float(volume)
            if not all(map(math.isfinite, (price, volume))) or price <= 0 or volume <= 0:
                return []
            parsed.append((ts, price, volume))
    except (TypeError, ValueError, OverflowError):
        return []
    if any(b[0] - a[0] != 900 for a, b in zip(parsed, parsed[1:])):
        return []
    return parsed


def _simulate(rows: list[tuple[int, float, float]], preset: dict,
              first: int, last: int, *, symbol: str = "SOL") -> dict:
    """Replay complete five-day competition windows through the live strategy.

    Signals, entries, exits, reversal, stops and final USDC liquidation all go
    through ``league_strategy.decide``. Orders fill at the next completed close;
    the only surrounding code models portfolio accounting, market impact and
    AudaceFi's rolling swap ceiling.
    """
    first, last = max(0, first), min(len(rows), last)
    if last - first < 100:
        return _metrics(STARTING_EQUITY_USDC, STARTING_EQUITY_USDC, [], 0,
                        0.0, 0.0, [], True)
    policy = policy_for("baseline", LeaguePolicy(
        max_position_weight=.35, max_total_exposure=.35,
        risk_budget_per_trade=.012, maximum_season_drawdown=.018,
        stop_distance_min=.025, stop_distance_max=.035,
        closing_buffer_hours=16, new_entry_cutoff_hours=24,
        maximum_signal_age_hours=.5, round_trip_cost_bps=30,
        minimum_net_edge=.004))
    # Apply only the candidate's signal thresholds/strategy; all risk and
    # execution constraints remain identical to the live policy.
    if preset.get("intraday_strategy") == "recovery":
        policy = replace(policy, intraday_strategy="recovery",
                         intraday_m3_min=preset["intraday_m3_min"],
                         intraday_m6_min=preset["intraday_m6_min"],
                         intraday_m12_min=preset["intraday_m12_min"],
                         intraday_m24_min=preset["intraday_m24_min"],
                         minimum_net_edge=preset["minimum_net_edge"])
    else:
        policy = replace(policy, intraday_strategy="momentum",
                         intraday_m3_min=preset.get("intraday_m3_min", policy.intraday_m3_min),
                         intraday_m6_min=preset.get("intraday_m6_min", policy.intraday_m6_min),
                         intraday_m12_min=preset.get("intraday_m12_min", policy.intraday_m12_min),
                         intraday_m24_min=preset.get("intraday_m24_min", policy.intraday_m24_min),
                         minimum_net_edge=preset.get("minimum_net_edge", policy.minimum_net_edge))

    equity = peak_all = STARTING_EQUITY_USDC
    max_drawdown = 0.0
    completed: list[float] = []
    costs = turnover = 0.0
    # Keep each simulated competition within the official maximum duration.
    # Equity and its reported drawdown curve continue across these windows.
    all_swaps: list[int] = []
    episode_starts = list(range(first, last, SEASON_CANDLES))
    for episode_begin in episode_starts:
        episode_end = min(last, episode_begin + SEASON_CANDLES)
        start_ts = rows[episode_begin][0]
        end_ts = rows[episode_end - 1][0] + 900
        if end_ts <= start_ts:
            continue
        cash = equity
        quantity = 0.0
        held_symbol = None
        entry_price = peak_price = 0.0
        entry_cash = 0.0
        season_peak = equity
        daily_peak = equity
        current_day = None
        pending = None
        for index in range(max(episode_begin, 96), episode_end):
            ts, close, _ = rows[index]
            now = datetime.fromtimestamp(ts + 900, tz=timezone.utc)
            day = now.date()
            if day != current_day:
                current_day, daily_peak = day, max(cash + quantity * close, cash)

            if pending is not None:
                action, order_time = pending
                if len([when for when in all_swaps
                        if ts - when < 24 * 3600]) < MAX_ROLLING_SWAPS:
                    if action["side"] == "BUY_WITH_USDC" and held_symbol is None:
                        notional = min(float(action["maximum_input_usdc"]), cash,
                                       cash * .35)
                        if notional >= 1:
                            fill = close * (1 + SLIPPAGE_PER_SWAP)
                            acquired = notional * (1 - FEE_PER_SWAP) / fill
                            cost = notional - acquired * close
                            cash -= notional
                            quantity, held_symbol = acquired, action["symbol"]
                            entry_price = fill
                            peak_price = close
                            entry_cash = cash + notional
                            costs += cost
                            turnover += notional
                            all_swaps.append(ts)
                    elif action["side"] == "SELL_TO_USDC" and held_symbol == action["symbol"]:
                        gross = quantity * close
                        proceeds = quantity * close * (1 - SLIPPAGE_PER_SWAP) * (1 - FEE_PER_SWAP)
                        cost = gross - proceeds
                        cash += proceeds
                        costs += cost
                        turnover += gross
                        net = cash / entry_cash - 1 if entry_cash else 0.0
                        completed.append(net)
                        quantity, held_symbol = 0.0, None
                        entry_price = peak_price = entry_cash = 0.0
                        all_swaps.append(ts)
                pending = None

            mark = cash + quantity * close
            equity = mark
            season_peak = max(season_peak, mark)
            daily_peak = max(daily_peak, mark)
            peak_all = max(peak_all, mark)
            max_drawdown = max(max_drawdown, 1 - mark / peak_all)
            if held_symbol:
                peak_price = max(peak_price, close)

            signal = signal_from_rows(held_symbol or symbol, rows, index)
            if signal is None:
                continue
            signals = [signal]
            portfolio = {"equity_usdc": mark, "cash_usdc": cash,
                "peak_equity_usdc": max(season_peak, daily_peak), "positions": {}}
            if held_symbol:
                portfolio["positions"][held_symbol] = {
                    "value_usdc": quantity * close, "price_usdc": close,
                    "entry_price_usdc": entry_price, "peak_price_usdc": max(entry_price, peak_price)}
            context = {"verified": True, "category": "crypto-bro",
                "restrictions_verified": True, "wallet_ready": True,
                "network": "solana-devnet", "season_status": "active",
                "start_utc": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
                "end_utc": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
                "eligible_symbols": [symbol], "max_position_weight": .35,
                "max_total_exposure": .35, "start_snapshot_confirmed": True}
            report = {"asset_class": "crypto", "model": "coinbase_completed_15m",
                "generated_at": now.isoformat(), "signals": signals}
            result = decide(context, portfolio, report, now=now, policy=policy)
            actions = result.get("actions", [])
            if actions:
                action = actions[0]
                rolling = [when for when in all_swaps if ts - when < 24 * 3600]
                if action["side"] == "BUY_WITH_USDC" and len(rolling) <= MAX_BUY_SWAPS_WITH_RESERVED_EXIT:
                    pending = (action, ts)
                elif action["side"] == "SELL_TO_USDC" and len(rolling) < MAX_ROLLING_SWAPS:
                    pending = (action, ts)

        # Explicitly invoke the same decision engine at the season boundary.
        if held_symbol and quantity > 0:
            close = rows[episode_end - 1][1]
            ts = rows[episode_end - 1][0] + 900
            now = datetime.fromtimestamp(end_ts, tz=timezone.utc)
            mark = cash + quantity * close
            signal = signal_from_rows(held_symbol, rows, episode_end - 1)
            context["season_status"] = "closed"
            result = decide(context, {"equity_usdc": mark, "cash_usdc": cash,
                "peak_equity_usdc": max(season_peak, daily_peak), "positions": {
                    held_symbol: {"value_usdc": quantity * close, "price_usdc": close,
                    "entry_price_usdc": entry_price, "peak_price_usdc": max(entry_price, peak_price)}}},
                {"asset_class": "crypto", "model": "coinbase_completed_15m",
                 "generated_at": now.isoformat(), "signals": [signal] if signal else []},
                now=now, policy=policy)
            if any(row.get("side") == "SELL_TO_USDC" for row in result.get("actions", [])):
                gross = quantity * close
                proceeds = gross * (1 - SLIPPAGE_PER_SWAP) * (1 - FEE_PER_SWAP)
                costs += gross - proceeds
                turnover += gross
                cash += proceeds
                completed.append(cash / entry_cash - 1 if entry_cash else 0.0)
                all_swaps.append(ts)
                quantity, held_symbol = 0.0, None
        equity = cash + quantity * (rows[episode_end - 1][1] if quantity else 0)
        peak_all = max(peak_all, equity)
        max_drawdown = max(max_drawdown, 1 - equity / peak_all)

    return _metrics(STARTING_EQUITY_USDC, equity, completed, max_drawdown,
                    costs, turnover, all_swaps, not held_symbol)


def _metrics(start: float, ending: float, completed: list[float],
             max_drawdown: float, costs: float, turnover: float,
             swap_times: list[int], usdc_only_at_end: bool) -> dict:
    rolling_max = max((sum(1 for when in swap_times if when <= at < when + 24*3600)
                       for at in swap_times), default=0)
    return {"trades": len(completed), "swaps": len(swap_times),
            "max_rolling_swaps_24h": rolling_max,
            "usdc_only_at_end": usdc_only_at_end,
            "net_return": round(ending / start - 1, 8),
            "net_pnl_usdc": round(ending - start, 4),
            "max_drawdown": round(max_drawdown, 8),
            "win_rate": round(sum(value > 0 for value in completed) / len(completed), 6)
            if completed else 0.0,
            "estimated_costs_usdc": round(costs, 4),
            "estimated_round_trip_cost_bps": round(ROUND_TRIP_COST * 10_000),
            "turnover_usdc": round(turnover, 4)}


def evaluate(candles_by_symbol: dict[str, object]) -> dict:
    """Train on past candles, score on a disjoint forward holdout.

    Candidate selection looks at TRAINING only. Holdout is then used as an
    admission gate (not as a fit target), and repeated live validations are
    separately required before the live runner adopts a variant.
    """
    if not isinstance(candles_by_symbol, dict):
        return {"status": "INSUFFICIENT_DATA"}
    train_candidates = []
    held_out: dict[tuple[str, str], tuple[list, int]] = {}
    for symbol in ("SOL", "ETH"):
        rows = validate_candles(candles_by_symbol.get(symbol))
        if not rows:
            continue
        # Training ends before holdout starts; each trade fully closes inside
        # its own segment. No future close can influence a train signal.
        split = len(rows) - 88
        train = {name: _simulate(rows, p, 96, split, symbol=symbol)
                 for name, p in PRESETS.items()}
        for name, result in train.items():
            if name == "recovery":
                # Recovery can only be enabled by the twelve-month holdout gate.
                continue
            if name == "baseline" or result["trades"] < MIN_TRAIN_TRADES or result["net_return"] <= 0:
                continue
            # Save validation data WITHOUT inspecting its performance during
            # selection. Select on training alone; then open the holdout once.
            train_candidates.append((result["net_return"], symbol, name, result))
            held_out[(symbol, name)] = (rows, split)
    if not train_candidates:
        return {"status": "NO_VALIDATED_IMPROVEMENT", "symbolsChecked": sorted(
            s for s in ("SOL", "ETH") if validate_candles(candles_by_symbol.get(s))
        )}
    # One candidate is nominated using training only (not best holdout P&L).
    winner = max(train_candidates, key=lambda item: (item[0], item[1], item[2]))
    train_return, symbol, name, training = winner
    rows, split = held_out[(symbol, name)]
    validation = _simulate(rows, PRESETS[name], split, len(rows), symbol=symbol)
    baseline = _simulate(rows, PRESETS["baseline"], split, len(rows), symbol=symbol)
    if not (validation["trades"] >= MIN_TEST_TRADES and
            validation["net_return"] > REQUIRED_TEST_EDGE and
            validation["net_return"] > baseline["net_return"] + REQUIRED_TEST_EDGE and
            validation["win_rate"] >= .5 and
            validation["max_drawdown"] <= .012 and
            training["max_drawdown"] <= .012):
        return {"status": "NO_VALIDATED_IMPROVEMENT", "symbolsChecked": sorted(
            s for s in ("SOL", "ETH") if validate_candles(candles_by_symbol.get(s))
        )}
    return {"status": "VALIDATED_CANDIDATE", "symbol": symbol, "profile": name,
            "train": training, "holdout": validation,
            "costBps": round(ROUND_TRIP_COST * 10000)}


def update_promotion(state: object, suggestion: dict, *, window: int) -> dict:
    """Short live samples may observe candidates but cannot authorize promotion."""
    if not isinstance(window, int) or window < 0:
        raise ValueError("invalid window")
    old = state if isinstance(state, dict) else {}
    active = old.get("active", "baseline")
    if active not in PRESETS:
        active = "baseline"
    prev = old.get("window", -1)
    # A short signal slice is useful for diagnostics but too short to establish
    # durable advantage. Only the verified 12-month qualification workflow
    # can set the recovery gate consumed by the executor.
    return {"active": active, "pending": None, "count": 0,
            "window": max(prev, window) if isinstance(prev, int) else window}
