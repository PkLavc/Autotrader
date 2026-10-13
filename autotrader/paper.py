from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from .config import StrategyConfig
from .portfolio import Target


@dataclass
class PaperState:
    cash: float
    positions: dict[str, float]
    nav_history: list[dict]
    trades: list[dict]
    last_prices: dict[str, float]
    benchmark_start_price: float | None = None
    benchmark_value: float | None = None
    last_market_date: str | None = None
    pending_targets: list[dict] = field(default_factory=list)
    pending_signal_date: str | None = None
    corporate_actions: list[dict] = field(default_factory=list)
    cumulative_costs: dict[str, float] = field(
        default_factory=lambda: {
            "slippage": 0.0,
            "commissions": 0.0,
            "regulatory_fees": 0.0,
            "cat_fees": 0.0,
            "dividend_income": 0.0,
            "taxes_withheld": 0.0,
            "tax_estimate": 0.0,
        }
    )
    tax_state: dict[str, Any] = field(default_factory=lambda: {
        "profile": "us_personal_no_local_tax",
        "tax_currency": "USD",
        "realized_gains_by_year": {},
        "realized_gains_by_month": {},
        "estimated_tax_by_year": {},
        "assumptions": [],
    })


def load_state(path: str, initial_cash: float) -> PaperState:
    p = Path(path)
    if not p.exists():
        return PaperState(initial_cash, {}, [], [], {})
    raw = json.loads(p.read_text(encoding="utf-8"))
    return PaperState(
        cash=float(raw["cash"]),
        positions={k: float(v) for k, v in raw.get("positions", {}).items()},
        nav_history=list(raw.get("nav_history", [])),
        trades=list(raw.get("trades", [])),
        last_prices={k: float(v) for k, v in raw.get("last_prices", {}).items()},
        benchmark_start_price=(
            float(raw["benchmark_start_price"])
            if raw.get("benchmark_start_price") is not None
            else None
        ),
        benchmark_value=(
            float(raw["benchmark_value"])
            if raw.get("benchmark_value") is not None
            else (
                float(raw.get("last_prices", {}).get("SPY")) / float(raw["benchmark_start_price"])
                if raw.get("benchmark_start_price") and raw.get("last_prices", {}).get("SPY")
                else None
            )
        ),
        last_market_date=raw.get("last_market_date"),
        pending_targets=list(raw.get("pending_targets", [])),
        pending_signal_date=raw.get("pending_signal_date"),
        corporate_actions=list(raw.get("corporate_actions", [])),
        cumulative_costs={
            "slippage": float(raw.get("cumulative_costs", {}).get("slippage", 0.0)),
            "commissions": float(raw.get("cumulative_costs", {}).get("commissions", 0.0)),
            "regulatory_fees": float(raw.get("cumulative_costs", {}).get("regulatory_fees", 0.0)),
            "cat_fees": float(raw.get("cumulative_costs", {}).get("cat_fees", 0.0)),
            "dividend_income": float(raw.get("cumulative_costs", {}).get("dividend_income", 0.0)),
            "taxes_withheld": float(raw.get("cumulative_costs", {}).get("taxes_withheld", 0.0)),
            "tax_estimate": float(raw.get("cumulative_costs", {}).get("tax_estimate", 0.0)),
        },
        tax_state=dict(raw.get("tax_state", {
            "profile": "us_personal_no_local_tax",
            "tax_currency": "USD",
            "realized_gains_by_year": {},
            "realized_gains_by_month": {},
            "estimated_tax_by_year": {},
            "assumptions": [],
        })),
    )


def _effective_price(state: PaperState, symbol: str, prices: dict[str, float]) -> float:
    if symbol in prices:
        return float(prices[symbol])
    if symbol in state.last_prices:
        return float(state.last_prices[symbol])
    raise RuntimeError(f"No current or prior price available for held symbol {symbol}")


def mark_to_market(state: PaperState, prices: dict[str, float]) -> float:
    return state.cash + sum(
        qty * _effective_price(state, sym, prices)
        for sym, qty in state.positions.items()
    )


def _commission(qty: float, notional: float, cfg: StrategyConfig) -> float:
    if qty <= 0 or notional <= 0:
        return 0.0
    if cfg.commission_per_share <= 0 and cfg.minimum_commission <= 0:
        return 0.0
    variable = qty * cfg.commission_per_share
    return min(max(variable, cfg.minimum_commission), notional * cfg.maximum_commission_rate)


def _regulatory_fees(side: str, qty: float, notional: float, cfg: StrategyConfig) -> dict[str, float]:
    """Approximate Alpaca US equity pass-through fees; actual totals aggregate per account/day."""
    if side == "SELL":
        sec_fee = math.ceil(notional * cfg.sec_fee_rate * 100 - 1e-10) / 100 if notional > 0 else 0.0
        taf_fee = math.ceil(min(qty * cfg.taf_fee_per_share, cfg.taf_fee_cap_per_order) * 100 - 1e-10) / 100
    else:
        sec_fee = 0.0
        taf_fee = 0.0
    cat_fee = math.ceil(qty * cfg.cat_fee_per_share * 100 - 1e-10) / 100 if qty > 0 else 0.0
    return {"sec_fee": sec_fee, "taf_fee": taf_fee, "cat_fee": cat_fee, "regulatory_fees": sec_fee + taf_fee + cat_fee}


def _record_realized_gain(state: PaperState, symbol: str, qty: float, price: float, fees: float, when: str | None) -> float:
    """Track realized USD gain with a moving average cost basis for the US paper ledger."""
    positions = state.tax_state.setdefault("cost_basis", {})
    current = positions.get(symbol, {"qty": 0.0, "cost": 0.0})
    held_qty = float(current.get("qty", 0.0))
    total_cost = float(current.get("cost", 0.0))
    if held_qty <= 1e-12:
        state.tax_state.setdefault("assumptions", [])
        if "initial_positions_cost_basis_unknown" not in state.tax_state["assumptions"]:
            state.tax_state["assumptions"].append("initial_positions_cost_basis_unknown")
        return 0.0
    used_qty = min(qty, held_qty)
    average_cost = total_cost / held_qty
    realized = used_qty * price - used_qty * average_cost - fees
    remaining_qty = held_qty - used_qty
    remaining_cost = total_cost - used_qty * average_cost
    if remaining_qty <= 1e-12:
        positions.pop(symbol, None)
    else:
        positions[symbol] = {"qty": remaining_qty, "cost": remaining_cost}
    if when:
        year = when[:4]
        month = when[:7]
        by_year = state.tax_state.setdefault("realized_gains_by_year", {})
        by_month = state.tax_state.setdefault("realized_gains_by_month", {})
        by_year[year] = float(by_year.get(year, 0.0)) + realized
        by_month[month] = float(by_month.get(month, 0.0)) + realized
    return realized


def _update_tax_estimate(state: PaperState, cfg: StrategyConfig, when: str | None) -> None:
    if not when:
        return
    year = when[:4]
    realized = float(state.tax_state.get("realized_gains_by_year", {}).get(year, 0.0))
    # US federal/state tax is user specific; no universal withholding is assumed.
    # This reserve is an explicitly configurable stress estimate, not a tax filing result.
    rate = max(0.0, float(getattr(cfg, "estimated_tax_rate", 0.0)))
    estimate = max(0.0, realized) * rate
    estimates = state.tax_state.setdefault("estimated_tax_by_year", {})
    previous = float(estimates.get(year, 0.0))
    estimates[year] = estimate
    state.cumulative_costs["tax_estimate"] += estimate - previous


def rebalance(
    state: PaperState,
    targets: list[Target],
    prices: dict[str, float],
    cfg: StrategyConfig,
    *,
    signal_date: str | None = None,
    execution_date: str | None = None,
    price_source: str = "close",
) -> list[dict]:
    nav = mark_to_market(state, prices)
    target_weights = {t.symbol: t.weight for t in targets}
    all_symbols = set(state.positions) | set(target_weights)
    trades: list[dict] = []
    slip = cfg.slippage_bps / 10_000.0

    desired_values = {sym: nav * target_weights.get(sym, 0.0) for sym in all_symbols}
    current_values = {
        sym: state.positions.get(sym, 0.0) * _effective_price(state, sym, prices)
        for sym in all_symbols
        if sym in state.positions
    }

    for sym in sorted(all_symbols):
        if sym not in prices:
            continue
        delta = desired_values.get(sym, 0.0) - current_values.get(sym, 0.0)
        if delta >= 0:
            continue
        if nav and abs(delta) / nav < cfg.rebalance_threshold:
            continue
        px = prices[sym] * (1.0 - slip)
        qty = min(state.positions.get(sym, 0.0), abs(delta) / px)
        if qty <= 0:
            continue
        notional = qty * px
        commission = _commission(qty, notional, cfg)
        fees = _regulatory_fees("SELL", qty, notional, cfg)
        realized_gain = _record_realized_gain(
            state, sym, qty, px, commission + fees["regulatory_fees"], execution_date or signal_date
        )
        state.positions[sym] = state.positions.get(sym, 0.0) - qty
        if state.positions[sym] <= 1e-12:
            state.positions.pop(sym, None)
        state.cash += notional - commission - fees["regulatory_fees"]
        slippage_cost = qty * prices[sym] * slip
        state.cumulative_costs["slippage"] += slippage_cost
        state.cumulative_costs["commissions"] += commission
        state.cumulative_costs["regulatory_fees"] += fees["regulatory_fees"]
        state.cumulative_costs["cat_fees"] += fees["cat_fee"]
        trades.append({
            "side": "SELL", "symbol": sym, "qty": qty, "price": px,
            "reference_price": prices[sym], "gross_value": notional,
            "commission": commission, "slippage_cost": slippage_cost,
            **fees, "realized_gain": realized_gain,
            "signal_date": signal_date, "execution_date": execution_date,
            "price_source": price_source,
        })

    for sym in sorted(target_weights, key=lambda s: target_weights[s], reverse=True):
        if sym not in prices:
            continue
        current = state.positions.get(sym, 0.0) * prices[sym]
        delta = desired_values[sym] - current
        if delta <= 0:
            continue
        if nav and abs(delta) / nav < cfg.rebalance_threshold:
            continue
        px = prices[sym] * (1.0 + slip)
        spend = min(delta, state.cash)
        qty = spend / px
        commission = _commission(qty, qty * px, cfg)
        if qty * px + commission > state.cash:
            qty = max(0.0, (state.cash - cfg.minimum_commission) / px)
            commission = _commission(qty, qty * px, cfg)
        if qty <= 0:
            continue
        notional = qty * px
        fees = _regulatory_fees("BUY", qty, notional, cfg)
        if notional + commission + fees["regulatory_fees"] > state.cash:
            qty = max(0.0, (state.cash - commission - fees["regulatory_fees"]) / px)
            notional = qty * px
            commission = _commission(qty, notional, cfg)
            fees = _regulatory_fees("BUY", qty, notional, cfg)
        if qty <= 0:
            continue
        state.tax_state.setdefault("cost_basis", {})
        basis = state.tax_state["cost_basis"].get(sym, {"qty": 0.0, "cost": 0.0})
        basis_qty = float(basis.get("qty", 0.0))
        basis_cost = float(basis.get("cost", 0.0))
        state.tax_state["cost_basis"][sym] = {
            "qty": basis_qty + qty,
            "cost": basis_cost + notional + commission + fees["regulatory_fees"],
        }
        state.positions[sym] = state.positions.get(sym, 0.0) + qty
        state.cash -= notional + commission + fees["regulatory_fees"]
        slippage_cost = qty * prices[sym] * slip
        state.cumulative_costs["slippage"] += slippage_cost
        state.cumulative_costs["commissions"] += commission
        state.cumulative_costs["regulatory_fees"] += fees["regulatory_fees"]
        state.cumulative_costs["cat_fees"] += fees["cat_fee"]
        trades.append({
            "side": "BUY", "symbol": sym, "qty": qty, "price": px,
            "reference_price": prices[sym], "gross_value": notional,
            "commission": commission, "slippage_cost": slippage_cost,
            **fees,
            "signal_date": signal_date, "execution_date": execution_date,
            "price_source": price_source,
        })

    if state.cash < -1e-7:
        raise RuntimeError(f"Paper account overspent cash: {state.cash}")

    now = datetime.now(timezone.utc).isoformat()
    for trade in trades:
        trade["timestamp"] = now
        state.trades.append(trade)
    _update_tax_estimate(state, cfg, execution_date or signal_date)
    return trades


def state_payload(state: PaperState) -> dict[str, Any]:
    return {
        "cash": state.cash,
        "positions": state.positions,
        "last_prices": state.last_prices,
        "benchmark_start_price": state.benchmark_start_price,
        "benchmark_value": state.benchmark_value,
        "last_market_date": state.last_market_date,
        "pending_targets": state.pending_targets,
        "pending_signal_date": state.pending_signal_date,
        "corporate_actions": state.corporate_actions[-5000:],
        "cumulative_costs": state.cumulative_costs,
        "tax_state": state.tax_state,
        "nav_history": state.nav_history[-1500:],
        "trades": state.trades[-5000:],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 2,
    }


def write_state(state: PaperState, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state_payload(state), indent=2, sort_keys=True), encoding="utf-8")


def save_state(
    state: PaperState,
    path: str,
    prices: dict[str, float],
    cfg: StrategyConfig,
    *,
    market_date: str | None = None,
    benchmark_session_return: float | None = None,
) -> float:
    state.last_prices.update({k: float(v) for k, v in prices.items() if math.isfinite(float(v))})
    if state.benchmark_start_price is None and cfg.benchmark_symbol in prices:
        state.benchmark_start_price = float(prices[cfg.benchmark_symbol])
    if state.benchmark_value is None:
        state.benchmark_value = 1.0
    elif benchmark_session_return is not None:
        state.benchmark_value *= 1.0 + float(benchmark_session_return)

    nav = mark_to_market(state, prices)
    now = datetime.now(timezone.utc).isoformat()
    state.last_market_date = market_date or state.last_market_date
    state.nav_history.append({
        "timestamp": now, "market_date": market_date,
        "nav": nav, "cash": state.cash,
    })
    write_state(state, path)
    return nav


def performance_metrics(state: PaperState, cfg: StrategyConfig, prices: dict[str, float]) -> dict:
    navs = [float(row["nav"]) for row in state.nav_history if float(row.get("nav", 0)) > 0]
    current_nav = navs[-1] if navs else cfg.initial_cash
    peak = 0.0
    max_drawdown = 0.0
    for nav in navs:
        peak = max(peak, nav)
        if peak > 0:
            max_drawdown = min(max_drawdown, (nav / peak) - 1.0)

    benchmark_return = None
    benchmark_now = prices.get(cfg.benchmark_symbol, state.last_prices.get(cfg.benchmark_symbol))
    if state.benchmark_value is not None:
        benchmark_return = state.benchmark_value - 1.0
    elif state.benchmark_start_price and benchmark_now:
        benchmark_return = (float(benchmark_now) / state.benchmark_start_price) - 1.0

    daily_returns = [
        navs[index] / navs[index - 1] - 1.0
        for index in range(1, len(navs))
        if navs[index - 1] > 0
    ]
    annualized_volatility = None
    sharpe = None
    if len(daily_returns) >= cfg.minimum_history_for_metrics:
        mean = sum(daily_returns) / len(daily_returns)
        variance = sum((value - mean) ** 2 for value in daily_returns) / (len(daily_returns) - 1)
        daily_vol = math.sqrt(variance)
        annualized_volatility = daily_vol * math.sqrt(252)
        if daily_vol > 0:
            sharpe = mean / daily_vol * math.sqrt(252)

    gross_traded = sum(float(row.get("gross_value", float(row.get("qty", 0)) * float(row.get("price", 0)))) for row in state.trades)
    gross_exposure = sum(
        qty * _effective_price(state, symbol, prices)
        for symbol, qty in state.positions.items()
    )

    return {
        "initial_cash": cfg.initial_cash,
        "current_nav": current_nav,
        "total_return": (current_nav / cfg.initial_cash) - 1.0,
        "max_drawdown": max_drawdown,
        "benchmark_symbol": cfg.benchmark_symbol,
        "benchmark_return": benchmark_return,
        "excess_return": ((current_nav / cfg.initial_cash) - 1.0 - benchmark_return) if benchmark_return is not None else None,
        "trade_count": len(state.trades),
        "observation_count": len(state.nav_history),
        "annualized_volatility": annualized_volatility,
        "sharpe_ratio": sharpe,
        "gross_exposure": gross_exposure,
        "gross_exposure_ratio": gross_exposure / current_nav if current_nav else None,
        "gross_traded": gross_traded,
        "turnover_on_initial_capital": gross_traded / cfg.initial_cash,
        "cumulative_costs": dict(state.cumulative_costs),
        "tax_estimate": {
            "profile": state.tax_state.get("profile", "us_personal_no_local_tax"),
            "year": (state.last_market_date or datetime.now(timezone.utc).date().isoformat())[:4],
            "estimated_tax_usd": None,
            "tax_reference_rate": 0.15 if state.tax_state.get("profile") == "brazil_foreign_investments_estimate" else None,
            "tax_currency": "BRL" if state.tax_state.get("profile") == "brazil_foreign_investments_estimate" else None,
            "realized_gain_usd": float(state.tax_state.get("realized_gains_by_year", {}).get((state.last_market_date or datetime.now(timezone.utc).date().isoformat())[:4], 0.0)),
            "status": "not_calculated_user_specific",
            "note": "Tax due is not calculated. US profile requires taxpayer-specific rules; Brazil profile requires complete tax lots and event-date BRL FX. Dividend withholding is not modeled.",
        },
    }


def infer_last_market_date(state: PaperState, benchmark_history: Any, tolerance: float = 1e-6) -> str | None:
    """Infer the legacy state's last session from its stored benchmark close."""
    prior = state.last_prices.get("SPY")
    if prior is None or benchmark_history is None or benchmark_history.empty:
        return None
    for timestamp, value in reversed(list(benchmark_history["close"].items())):
        if math.isclose(float(value), float(prior), rel_tol=tolerance, abs_tol=tolerance):
            return timestamp.date().isoformat()
    return None


def apply_corporate_actions(
    state: PaperState,
    histories: dict[str, Any],
    start_date: str | None,
    end_date: str,
) -> list[dict]:
    """Apply splits and cash dividends once for positions held across sessions."""
    if not start_date:
        return []
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    applied: list[dict] = []
    known = {row.get("id") for row in state.corporate_actions}
    for symbol in sorted(list(state.positions)):
        history = histories.get(symbol)
        if history is None:
            continue
        rows = history[(history.index.date > start) & (history.index.date <= end)]
        for timestamp, row in rows.iterrows():
            session = timestamp.date().isoformat()
            split = float(row.get("stock_splits", 0.0) or 0.0)
            split_id = f"{session}:{symbol}:split"
            if split > 0 and split_id not in known:
                before = state.positions.get(symbol, 0.0)
                state.positions[symbol] = before * split
                basis = state.tax_state.get("cost_basis", {}).get(symbol)
                if basis:
                    basis["qty"] = float(basis.get("qty", 0.0)) * split
                action = {
                    "id": split_id, "market_date": session, "symbol": symbol,
                    "type": "SPLIT", "ratio": split, "quantity_before": before,
                    "quantity_after": state.positions[symbol],
                }
                state.corporate_actions.append(action)
                applied.append(action)
                known.add(split_id)
            dividend = float(row.get("dividends", 0.0) or 0.0)
            dividend_id = f"{session}:{symbol}:dividend"
            if dividend > 0 and dividend_id not in known:
                quantity = state.positions.get(symbol, 0.0)
                cash_amount = quantity * dividend
                state.cash += cash_amount
                state.cumulative_costs.setdefault("dividend_income", 0.0)
                state.cumulative_costs["dividend_income"] += cash_amount
                action = {
                    "id": dividend_id, "market_date": session, "symbol": symbol,
                    "type": "DIVIDEND", "per_share": dividend,
                    "quantity": quantity, "cash_amount": cash_amount,
                    "tax_status": "gross_dividend_tax_withholding_not_modeled",
                }
                state.corporate_actions.append(action)
                applied.append(action)
                known.add(dividend_id)
    return applied
