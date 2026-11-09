"""Translate verified historical research into a narrow live-strategy gate."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from .adaptive_learning import PRESETS


def qualification(report: dict, run_id: str) -> dict:
    results = report.get("results") if isinstance(report, dict) else None
    if not isinstance(results, list):
        raise ValueError("HISTORICAL_REPORT_INVALID")
    promotable = set(PRESETS) - {"baseline"}
    qualified: dict[str, dict[str, float]] = {"SOL": {}, "ETH": {}}
    for row in results:
        if (not isinstance(row, dict) or row.get("symbol") not in {"SOL", "ETH"} or
                row.get("status") != "VALIDATED_HISTORICAL_CANDIDATE" or
                row.get("profile") not in promotable or
                not isinstance(row.get("comparison"), dict)):
            continue
        profile = row["profile"]
        candidate = row["comparison"].get(profile, {})
        baseline = row["comparison"].get("baseline", {})
        candidate = candidate if isinstance(candidate, dict) else {}
        baseline = baseline if isinstance(baseline, dict) else {}
        train, holdout = candidate.get("train", {}), candidate.get("holdout", {})
        base_train, base_holdout = baseline.get("train", {}), baseline.get("holdout", {})
        consistency = row.get("holdoutConsistency", {})
        consistency = consistency if isinstance(consistency, dict) else {}
        regimes = consistency.get("regimes", {}) if isinstance(consistency, dict) else {}
        if (holdout.get("net_return", 0) > base_holdout.get("net_return", 0) + .003 and
                train.get("net_return", 0) > base_train.get("net_return", 0) and
                train.get("trades", 0) >= 10 and holdout.get("net_return", 0) > 0 and
                holdout.get("trades", 0) >= 6 and
                train.get("max_rolling_swaps_24h", 11) <= 10 and
                holdout.get("max_rolling_swaps_24h", 11) <= 10 and
                holdout.get("usdc_only_at_end") is True and
                holdout.get("max_intraday_drawdown", 1) <= .018 and
                train.get("max_intraday_drawdown", 1) <= .018 and
                holdout.get("max_competition_drawdown", 1) <= .018 and
                train.get("max_competition_drawdown", 1) <= .018 and
                consistency.get("folds", 0) >= 10 and
                consistency.get("wilsonLower95", 0) > .50 and
                consistency.get("blockBootstrapMeanEdgeLower95", 0) > 0 and
                consistency.get("regimeCoverage", 0) >= 2 and
                isinstance(regimes, dict) and regimes and
                all(isinstance(item, dict) and item.get("folds", 0) >= 3 and
                    item.get("meanEdge", -1) >= 0
                    for item in regimes.values())):
            edge = holdout.get("net_return", 0) - base_holdout.get("net_return", 0)
            qualified[row["symbol"]][profile] = edge
    # A globally approved policy must have independent qualification for both
    # live-supported assets. One token's holdout cannot authorize the other.
    shared = set(qualified["SOL"]) & set(qualified["ETH"])
    selected = max(shared, key=lambda name: (
        qualified["SOL"][name] + qualified["ETH"][name], name)) if shared else None
    approved = selected is not None
    try:
        source = int(run_id)
    except (TypeError, ValueError):
        raise ValueError("HISTORICAL_RUN_ID_INVALID") from None
    return {"schemaVersion": 2,
            "approvedStrategy": selected if approved else "momentum",
            "sourceRunId": source if approved else None,
            "approvedSymbols": ["SOL", "ETH"] if approved else [],
            "reason": "VALIDATED_12M_OOS_ADVANTAGE" if approved else
                      "NO_12M_OOS_CANDIDATE_ADVANTAGE"}


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
