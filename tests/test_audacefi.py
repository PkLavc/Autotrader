from datetime import datetime, timedelta, timezone
import pytest

from integrations.audacefi.prepare import prepare

MINT_USDC = "A" * 32
MINT_SOL = "B" * 32
WALLET = "C" * 32
SEASON = "12345678-1234-1234-1234-123456789abc"


def settings():
    return {
        "seasonId": SEASON,
        "category": "degen",
        "playerPublicKey": WALLET,
        "usdcMainnet": MINT_USDC,
        "buyAmountUsdc": "2.00",
        "maxInputUsdc": "5",
        "minExpectedReturn": "0.02",
        "maxExpectedReturn": "0.30",
        "tokens": {"SOL": MINT_SOL},
    }


def report(now, signals=None):
    return {
        "asset_class": "crypto",
        "generated_at": now.isoformat(),
        "signals": signals if signals is not None else [{"symbol": "SOL", "expected_return": 0.06}],
    }


def test_equity_report_is_blocked():
    assert prepare({"targets": [{"symbol": "AAPL"}]}, settings())["reason"] == "EQUITY_REPORT_NOT_CRYPTO"


def test_missing_account_is_hold():
    now = datetime.now(timezone.utc)
    assert prepare(report(now), None, now=now)["status"] == "HOLD"


def test_valid_crypto_proposal_is_not_execution():
    now = datetime.now(timezone.utc)
    one = prepare(report(now), settings(), now=now)
    two = prepare(report(now), settings(), now=now)
    assert one == two
    assert one["status"] == "PREPARED"
    assert one["executionAuthorized"] is False
    assert one["operation"]["amountInRaw"] == "2000000"
    assert one["operation"]["tokenOutMint"] == MINT_SOL
    assert one["operation"]["inputDecimals"] == 6


def test_unknown_token_is_hold():
    now = datetime.now(timezone.utc)
    result = prepare(report(now, [{"symbol": "AAPL", "expected_return": .25}]), settings(), now=now)
    assert result["reason"] == "NO_ELIGIBLE_CRYPTO_SIGNAL"


def test_stale_data_is_hold():
    now = datetime.now(timezone.utc)
    result = prepare(report(now - timedelta(days=1)), settings(), now=now)
    assert result["reason"] == "CRYPTO_SIGNAL_STALE"


def test_limit_violation_fails_closed():
    now = datetime.now(timezone.utc)
    cfg = settings()
    cfg["buyAmountUsdc"] = "8"
    with pytest.raises(ValueError):
        prepare(report(now), cfg, now=now)


def test_nonfinite_score_fails_closed():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        prepare(report(now, [{"symbol": "SOL", "expected_return": "NaN"}]), settings(), now=now)


def test_fractional_raw_unit_fails_closed():
    now = datetime.now(timezone.utc)
    cfg = settings()
    cfg["buyAmountUsdc"] = "0.0000001"
    with pytest.raises(ValueError):
        prepare(report(now), cfg, now=now)


def test_future_timestamp_is_hold():
    now = datetime.now(timezone.utc)
    assert prepare(report(now + timedelta(hours=2)), settings(), now=now)["status"] == "HOLD"
