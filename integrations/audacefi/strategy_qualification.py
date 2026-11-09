"""Translate verified historical research into a narrow live-strategy gate."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


def qualification(report: dict, run_id: str) -> dict:
    results = report.get("results") if isinstance(report, dict) else None
    if not isinstance(results, list):
        raise ValueError("HISTORICAL_REPORT_INVALID")
    approved = any(
        isinstance(row, dict) and row.get("symbol") in {"SOL", "ETH"} and
        row.get("status") == "VALIDATED_HISTORICAL_CANDIDATE" and
        row.get("profile") == "recovery" and
        isinstance(row.get("comparison"), dict) and
        row["comparison"].get("recovery", {}).get("holdout", {}).get("net_return", 0) >
        row["comparison"].get("baseline", {}).get("holdout", {}).get("net_return", 0) + .003 and
        row["comparison"].get("recovery", {}).get("train", {}).get("net_return", 0) >
        row["comparison"].get("baseline", {}).get("train", {}).get("net_return", 0) and
        row["comparison"].get("recovery", {}).get("train", {}).get("trades", 0) >= 10 and
        row["comparison"].get("recovery", {}).get("holdout", {}).get("net_return", 0) > 0 and
        row["comparison"].get("recovery", {}).get("holdout", {}).get("trades", 0) >= 6 and
        row["comparison"].get("recovery", {}).get("train", {}).get("max_rolling_swaps_24h", 11) <= 10 and
        row["comparison"].get("recovery", {}).get("holdout", {}).get("max_rolling_swaps_24h", 11) <= 10 and
        row["comparison"].get("recovery", {}).get("holdout", {}).get("usdc_only_at_end") is True and
        row["comparison"].get("recovery", {}).get("holdout", {}).get("max_intraday_drawdown", 1) <= .018 and
        row["comparison"].get("recovery", {}).get("train", {}).get("max_intraday_drawdown", 1) <= .018 and
        row["comparison"].get("recovery", {}).get("holdout", {}).get("max_competition_drawdown", 1) <= .018 and
        row["comparison"].get("recovery", {}).get("train", {}).get("max_competition_drawdown", 1) <= .018
        for row in results
    )
    try:
        source = int(run_id)
    except (TypeError, ValueError):
        raise ValueError("HISTORICAL_RUN_ID_INVALID") from None
    return {"schemaVersion": 1,
            "approvedStrategy": "recovery" if approved else "momentum",
            "sourceRunId": source if approved else None,
            "reason": "VALIDATED_12M_OOS_ADVANTAGE" if approved else
                      "NO_12M_OOS_RECOVERY_ADVANTAGE"}


def main() -> int:
    report_path = Path(os.environ.get("AUDACEFI_HISTORY_REPORT",
                                      "audacefi-historical-qualification.json"))
    output_path = Path(os.environ.get("AUDACEFI_STRATEGY_QUALIFICATION",
                                     "integrations/audacefi/strategy-qualification.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    result = qualification(report, os.environ.get("GITHUB_RUN_ID", ""))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".qualification-", dir=output_path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("AUDACEFI_HISTORICAL_STRATEGY=" + result["approvedStrategy"])
    print("AUDACEFI_HISTORICAL_STRATEGY_REASON=" + result["reason"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
