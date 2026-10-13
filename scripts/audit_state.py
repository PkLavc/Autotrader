from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(f"state audit failed: {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Autotrader state invariants")
    parser.add_argument("state", nargs="?", default="data/state.json")
    parser.add_argument("report", nargs="?", default="data/latest.json")
    parser.add_argument("--require-v2", action="store_true")
    args = parser.parse_args()
    state_path = Path(args.state)
    report_path = Path(args.report)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))

    cash = float(state["cash"])
    if not math.isfinite(cash) or cash < -1e-6:
        fail(f"invalid cash {cash}")
    for symbol, qty in state.get("positions", {}).items():
        if not symbol or not math.isfinite(float(qty)) or float(qty) <= 0:
            fail(f"invalid position {symbol}={qty}")
    if state.get("positions", {}) != report.get("positions", {}):
        fail("state/report positions differ")
    market_date = state.get("last_market_date")
    report_date = report.get("market_session")
    if args.require_v2 and (not market_date or not report_date):
        fail("v2 market session metadata is required")
    if market_date != report_date:
        fail(f"state/report market session mismatch: {market_date} != {report.get('market_session')}")
    history = state.get("nav_history", [])
    if not history:
        fail("NAV history is empty")
    if not math.isclose(float(history[-1]["nav"]), float(report["nav"]), rel_tol=1e-10, abs_tol=1e-7):
        fail("state/report NAV differs")
    if not math.isclose(cash, float(report["cash"]), rel_tol=1e-10, abs_tol=1e-7):
        fail("state/report cash differs")
    session_rows = [row.get("market_date") for row in history if row.get("market_date")]
    if len(session_rows) != len(set(session_rows)):
        fail("duplicate market sessions in NAV history")
    order_ids = [row.get("client_order_id") for row in state.get("trades", []) if row.get("client_order_id")]
    if len(order_ids) != len(set(order_ids)):
        fail("duplicate client order identifiers")
    targets = state.get("pending_targets", [])
    weights = [float(row["weight"]) for row in targets]
    if any(weight < 0 or weight > 0.18 + 1e-12 for weight in weights) or sum(weights) > 0.90 + 1e-12:
        fail("pending targets violate exposure limits")
    for name, value in state.get("cumulative_costs", {}).items():
        if not math.isfinite(float(value)) or float(value) < 0:
            fail(f"invalid cumulative cost {name}={value}")
    if report.get("mode") != "paper":
        fail("strategy report is not paper mode")
    print(json.dumps({
        "status": "ok" if market_date else "legacy_ok_pending_v2_migration",
        "market_session": market_date,
        "positions": len(state.get("positions", {})),
        "observations": len(history),
        "trades": len(state.get("trades", [])),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
