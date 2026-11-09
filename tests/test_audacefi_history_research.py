"""AudaceFi verified historical loader + stock learning reuse tests.

Synthetic archives only; test runner needs no Binance/API credentials.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from integrations.audacefi import history_job, history_research


def archive(start=1_730_000_700, length=320, change=.0005):
    start -= start % 900
    csv_rows = [
        ",".join([
            str((start + i * 900) * 1_000_000), "100", "101", "99",
            f"{100 * (1 + change) ** i:.7f}", "500000", "1", "1", "1", "1", "1", "0",
        ])
        for i in range(length)
    ]
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as z:
        z.writestr("SOLUSDT-15m-test.csv", "\n".join(csv_rows) + "\n")
    data = buffer.getvalue()
    return data, sha256(data).hexdigest(), start


def test_months_are_closed_utc_only():
    assert history_research.complete_months(datetime(2026, 11, 9, tzinfo=timezone.utc)) == [
        "2026-07", "2026-08", "2026-09", "2026-10"]
    assert history_research.complete_months(datetime(2026, 1, 10, tzinfo=timezone.utc), 2) == [
        "2025-11", "2025-12"]


def test_binance_official_archives_use_expected_paths():
    url = history_research.archive_url("SOL", "2026-10")
    assert url == (
        "https://data.binance.vision/data/spot/monthly/klines/"
        "SOLUSDT/15m/SOLUSDT-15m-2026-10.zip"
    )
    with pytest.raises(ValueError):
        history_research.archive_url("../../secrets", "2026-10")
    assert "SOLUSDC/15m/SOLUSDC" in history_research.archive_url("SOL", "2026-10", "USDC")
    with pytest.raises(ValueError, match="UNSUPPORTED_QUOTE"):
        history_research.archive_url("SOL", "2026-10", "BUSD")


def test_archive_integrity_microsecond_timestamps_and_rows():
    zipped, checksum, start = archive()
    rows = history_research.parse_archive(zipped, checksum)
    assert len(rows) == 320
    assert rows[0][0] == start
    assert rows[-1][0] == start + 319 * 900
    assert all(row[1] > 0 for row in rows)


def test_zip_checksum_is_mandatory_and_detects_tampering():
    zipped, checksum, _ = archive()
    with pytest.raises(ValueError, match="MISMATCH"):
        history_research.parse_archive(zipped + b"bad", checksum)
    with pytest.raises(ValueError, match="MISSING"):
        history_research.parse_archive(zipped, "bad")


def test_loader_refuses_gap_between_months():
    z1, h1, _ = archive()
    z2, h2, _ = archive(start=1_730_800_000)
    urls = {
        history_research.archive_url("SOL", "2026-08"): z1,
        history_research.archive_url("SOL", "2026-08") + ".CHECKSUM": (h1 + "  file.zip").encode(),
        history_research.archive_url("SOL", "2026-09"): z2,
        history_research.archive_url("SOL", "2026-09") + ".CHECKSUM": (h2 + "  file.zip").encode(),
    }
    with pytest.raises(ValueError, match="MONTH_BOUNDARY_GAP"):
        history_research.load_history("SOL", ["2026-08", "2026-09"],
                                      loader=lambda url, _: urls[url])


def test_short_history_is_not_automatically_qualified():
    assert history_research.walk_forward([[i * 900, 100, 5000]
                                          for i in range(1000)])["status"] == "INSUFFICIENT_HISTORY"


def test_chronological_walk_forward_preserves_holdout():
    values = [[(1_700_000_100 - 1_700_000_100 % 900) + i * 900,
               100 * 1.0005 ** i, 500000] for i in range(320 * 9)]
    result = history_research.walk_forward(values)
    assert result["folds"] == 9
    assert result["status"] in {"NO_TRAINING_EDGE", "REJECTED_OOS",
                                "VALIDATED_HISTORICAL_CANDIDATE"}


def test_existing_autotrader_bayesian_calibration_is_reused():
    values = [[(1_700_000_100 - 1_700_000_100 % 900) + i * 900,
               100 * 1.0006 ** i, 500000] for i in range(320 * 33)]
    factor = history_job.stock_learning_calibration(values, "SOL")
    assert 0.95 <= factor <= 1.05


def test_api_unavailable_does_not_fabricate_eligible_symbols(monkeypatch):
    monkeypatch.setattr(history_job, "read_route", lambda key, route: (403, None))
    with pytest.raises(RuntimeError, match="HISTORY_SEASON_UNVERIFIED"):
        history_job.eligible_symbols("fake")
