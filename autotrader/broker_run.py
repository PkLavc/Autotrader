from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any

from .broker import AlpacaClient, BrokerLimits, plan_rebalance


LIVE_CONFIRMATION = "I_UNDERSTAND_THIS_PLACES_REAL_MONEY_ORDERS"


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


def _write_report(path: str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely rebalance an Alpaca paper/live account")
    parser.add_argument("--signal", default="data/latest.json")
    parser.add_argument("--output", default="data/broker-latest.json")
    parser.add_argument("--execute", action="store_true", help="Submit orders; default is a dry run")
    args = parser.parse_args()

    mode = os.getenv("AUTOTRADER_BROKER_MODE", "paper").strip().lower()
    if mode not in {"paper", "live"}:
        raise RuntimeError("AUTOTRADER_BROKER_MODE must be paper or live")
    if mode == "live":
        if os.getenv("AUTOTRADER_ALLOW_LIVE", "").lower() != "true":
            raise RuntimeError("live mode is locked; AUTOTRADER_ALLOW_LIVE must be true")
        if os.getenv("AUTOTRADER_LIVE_CONFIRMATION") != LIVE_CONFIRMATION:
            raise RuntimeError("live mode confirmation phrase is missing")

    signal = json.loads(Path(args.signal).read_text(encoding="utf-8"))
    signal_date = str(signal.get("market_session") or "")
    if not signal_date:
        raise RuntimeError("signal report has no market_session; generate a v2 report first")
    age = (date.today() - date.fromisoformat(signal_date)).days
    max_age = int(os.getenv("AUTOTRADER_MAX_SIGNAL_AGE_DAYS", "7"))
    if age < 0 or age > max_age:
        raise RuntimeError(f"signal date {signal_date} is stale or invalid (age={age})")

    client = AlpacaClient(
        os.getenv("ALPACA_API_KEY", ""),
        os.getenv("ALPACA_API_SECRET", ""),
        paper=mode == "paper",
    )
    account = client.account()
    if str(account.get("status", "")).upper() != "ACTIVE":
        raise RuntimeError(f"broker account is not active: {account.get('status')}")
    if account.get("trading_blocked") or account.get("account_blocked"):
        raise RuntimeError("broker account is blocked from trading")

    clock = client.clock()
    limits = BrokerLimits(
        max_order_notional=_float_env("AUTOTRADER_MAX_ORDER_NOTIONAL", 100.0),
        max_total_notional=_float_env("AUTOTRADER_MAX_TOTAL_NOTIONAL", 500.0),
        rebalance_threshold=_float_env("AUTOTRADER_BROKER_REBALANCE_THRESHOLD", 0.03),
        max_target_weight=_float_env("AUTOTRADER_MAX_TARGET_WEIGHT", 0.18),
        max_gross_target=_float_env("AUTOTRADER_MAX_GROSS_TARGET", 0.90),
    )
    orders = plan_rebalance(
        equity=float(account["equity"]),
        cash=max(0.0, float(account["cash"])),
        positions=client.positions(),
        targets=list(signal.get("targets", [])),
        signal_date=signal_date,
        limits=limits,
    )

    if args.execute and not bool(clock.get("is_open")):
        raise RuntimeError("broker market clock is closed; refusing to submit market orders")

    results: list[dict[str, Any]] = []
    submitted_indexes: list[int] = []
    for order in orders:
        existing = client.order_by_client_id(order.client_order_id)
        if existing:
            results.append({
                "client_order_id": order.client_order_id,
                "symbol": order.symbol,
                "side": order.side,
                "status": "duplicate_prevented",
                "broker_status": existing.get("status"),
            })
            continue
        if args.execute:
            submitted = client.submit(order)
            results.append({
                "client_order_id": order.client_order_id,
                "symbol": order.symbol,
                "side": order.side,
                "estimated_notional": order.estimated_notional,
                "status": "submitted",
                "broker_status": submitted.get("status"),
                "broker_order_id": submitted.get("id"),
            })
            submitted_indexes.append(len(results) - 1)
        else:
            results.append({
                "client_order_id": order.client_order_id,
                "symbol": order.symbol,
                "side": order.side,
                "estimated_notional": order.estimated_notional,
                "status": "dry_run",
                "payload": order.alpaca_payload(),
            })

    terminal = {"filled", "canceled", "expired", "rejected", "replaced"}
    if args.execute and submitted_indexes:
        deadline = time.monotonic() + 30
        pending = set(submitted_indexes)
        while pending and time.monotonic() < deadline:
            for index in list(pending):
                order_id = results[index].get("broker_order_id")
                if not order_id:
                    results[index]["status"] = "missing_broker_order_id"
                    pending.remove(index)
                    continue
                broker_order = client.order(str(order_id))
                status = str(broker_order.get("status", "unknown"))
                results[index]["broker_status"] = status
                results[index]["filled_qty"] = broker_order.get("filled_qty")
                results[index]["filled_avg_price"] = broker_order.get("filled_avg_price")
                if status in terminal:
                    results[index]["status"] = "filled" if status == "filled" else "terminal_without_full_fill"
                    pending.remove(index)
            if pending:
                time.sleep(2)
        for index in pending:
            results[index]["status"] = "fill_unconfirmed_before_timeout"

    final_account = client.account()
    final_positions = client.positions()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "broker": "alpaca",
        "mode": mode,
        "execution_enabled": args.execute,
        "signal_date": signal_date,
        "market_open": bool(clock.get("is_open")),
        "equity": float(account["equity"]),
        "cash": float(account["cash"]),
        "limits": limits.__dict__,
        "orders": results,
        "post_trade": {
            "equity": float(final_account["equity"]),
            "cash": float(final_account["cash"]),
            "positions": [
                {
                    "symbol": position.symbol,
                    "qty": position.qty,
                    "market_value": position.market_value,
                }
                for position in final_positions
            ],
        },
    }
    _write_report(args.output, report)
    print(json.dumps(report, indent=2))
    bad_statuses = {
        "terminal_without_full_fill",
        "missing_broker_order_id",
        "fill_unconfirmed_before_timeout",
    }
    bad_broker_statuses = {"canceled", "expired", "rejected"}
    failed = any(
        row.get("status") in bad_statuses or row.get("broker_status") in bad_broker_statuses
        for row in results
    )
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
