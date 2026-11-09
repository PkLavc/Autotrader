from datetime import datetime, timezone
import json

import pandas as pd

from integrations.audacefi.oracle_kronos_shadow import run_shadow


def test_kronos_shadow_records_once_and_scores_matured_predictions(tmp_path):
    now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    dates = pd.date_range("2026-11-04", "2026-11-09", freq="D")
    closes = [100.0, 105.0, 110.0, 115.0, 120.0, 130.0]
    histories = {symbol: pd.DataFrame({"close": closes}, index=dates)
                 for symbol in ("WBTC", "ETH", "SOL")}
    state_path = tmp_path / "shadow.json"
    state_path.write_text('''{"schemaVersion":1,"predictions":[{
      "symbol":"ETH","forecast_date":"2026-11-04","horizon_days":5,
      "kronos_return":0.2,"momentum_5d_return":0.1,"actual_return_5d":null}]}''',
                          encoding="utf-8")

    def forecast_runner(_, histories):
        return {"model": "NeoQuasar/Kronos-base", "model_revision": "model-pin",
                "tokenizer_revision": "tokenizer-pin", "signals": [
                    {"symbol": symbol, "last_completed_candle_utc": "2026-11-09",
                     "current_close_usd": 130.0, "expected_return": 0.03,
                     "baseline_momentum_5d": 0.25}
                    for symbol in ("WBTC", "ETH", "SOL")]}

    first = run_shadow(now=now, state_path=state_path,
                       history_loader=lambda _: histories,
                       forecast_runner=forecast_runner)
    assert first["newlyMatured"] == 1
    assert first["newPredictions"] == 3
    assert first["executionAuthorized"] is False

    saved = json.loads(state_path.read_text(encoding="utf-8"))
    matured = saved["predictions"][0]
    assert matured["actual_return_5d"] == 0.3
    assert matured["kronos_beat_momentum"] is True
    assert saved["evaluation"]["maturedAssetPredictions"] == 1

    second = run_shadow(now=now, state_path=state_path,
                        history_loader=lambda _: histories,
                        forecast_runner=forecast_runner)
    assert second["newPredictions"] == 0
    assert len(json.loads(state_path.read_text(encoding="utf-8"))["predictions"]) == 4
