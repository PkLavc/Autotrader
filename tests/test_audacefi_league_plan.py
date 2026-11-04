from datetime import datetime, timezone

from integrations.audacefi.league_plan import build_plan
NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


def context(**changes):
    base = {
        "verified": True, "restrictions_verified": True,
        "category": "crypto-bro", "wallet_ready": True,
        "network": "solana-devnet", "season_status": "active",
        "start_utc": "2026-11-09T00:00:00Z",
        "end_utc": "2026-11-14T00:00:00Z",
        "start_snapshot_confirmed": True,
        "eligible_symbols": ["BTC", "ETH", "SOL"],
        "max_position_weight": .50, "max_total_exposure": .85,
    }
    base.update(changes)
    return base


def wallet(**changes):
    base = {"equity_usdc": 1000, "peak_equity_usdc": 1020,
            "cash_usdc": 1000, "positions": {}}
    base.update(changes)
    return base


def signals(**changes):
    base = {"asset_class": "crypto", "generated_at": NOW.isoformat(),
            "signals": [{"symbol": "SOL", "expected_return": .12,
                         "momentum_3d": .04, "momentum_7d": .08,
                         "volatility_annualized": .8,
                         "avg_dollar_volume_usd": 250000000}]}
    base.update(changes)
    return base


SEASON = "12345678-1234-1234-1234-123456789abc"
ADDRESS = "C" * 32
MINTS = {"BTC": "D" * 32, "ETH": "E" * 32, "SOL": "B" * 32}
USDC = "A" * 32


def verified_context(**update):
    ctx = context(
        season_id=SEASON, wallet_address=ADDRESS,
        approved_token_mints=MINTS, approved_usdc_mint=USDC,
    )
    ctx.update(update)
    return ctx


def settings(**update):
    obj = {
        "seasonId": SEASON,
        "category": "crypto-bro",
        "playerPublicKey": ADDRESS,
        "usdcMainnet": USDC,
        "buyAmountUsdc": "40",
        "maxInputUsdc": "50",
        "minExpectedReturn": "0.02",
        "maxExpectedReturn": "0.30",
        "tokens": MINTS,
    }
    obj.update(update)
    return obj


def test_league_plan_wraps_supported_operation_and_stays_unsigned():
    result = build_plan(verified_context(), wallet(), signals(), settings(), now=NOW)
    assert result["status"] == "PREPARED"
    assert result["executionAuthorized"] is False
    assert result["operation"]["amountInRaw"] == "40000000"
    assert result["operation"]["tokenOutMint"] in MINTS.values()
    assert result["league"]["model"] == "league_crypto_bro_v1"


def test_league_cannot_prepare_trade_with_unverified_mint_or_wallet():
    bad_mint = settings(tokens={**MINTS, "SOL": "F" * 32})
    assert build_plan(verified_context(), wallet(), signals(), bad_mint, now=NOW)["status"] == "HOLD"
    assert build_plan(verified_context(wallet_address="Z" * 32),
                      wallet(), signals(), settings(), now=NOW)["status"] == "HOLD"


def test_snapshot_exit_is_not_misrepresented_as_buy():
    almost_end = datetime(2026, 11, 13, 19, tzinfo=timezone.utc)
    portfolio = wallet(cash_usdc=700, positions={
        "SOL": {"value_usdc": 300, "price_usdc": 110,
                "entry_price_usdc": 100, "peak_price_usdc": 115}})
    result = build_plan(verified_context(), portfolio, {}, settings(), now=almost_end)
    assert result["status"] == "EXIT_REQUIRED"
    assert result["actions"][0]["side"] == "SELL_TO_USDC"
    assert "operation" not in result
