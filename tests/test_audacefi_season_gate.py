"""Offline behavior tests for AudaceFi season watcher."""
from datetime import datetime, timezone
import unittest

from integrations.audacefi.season_gate import classify

NOW = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)


def example(status="active", execution="devnet-onchain", wallet="ok"):
    return {
        "season": {"active": {
            "id": "test", "status": status, "executionMode": execution,
            "startsAt": "2026-11-03T21:00:00Z",
            "endsAt": "2026-12-03T21:00:00Z",
        }},
        "wallet": {
            "state": wallet, "category": "crypto-bro",
            "walletAddress": "TEST_PUBLIC_ADDRESS",
        },
    }


class SeasonGateTests(unittest.TestCase):
    def test_wait_for_season(self):
        self.assertEqual(classify({"season": {"active": None}}, now=NOW), "WAITING_FOR_SEASON")

    def test_reject_malformed_responses(self):
        self.assertEqual(classify({"error": "unexpected"}, now=NOW), "HOLD_UNRECOGNIZED_SEASON")
        self.assertEqual(classify({"season": {"active": {}}}, now=NOW), "HOLD_INVALID_SEASON_STATUS")

    def test_reject_future_and_closed_seasons(self):
        self.assertEqual(classify(example("scheduled"), now=NOW), "WAITING_FOR_SEASON")
        self.assertEqual(classify(example(), now=datetime(2026, 11, 3, 20, tzinfo=timezone.utc)), "WAITING_FOR_SEASON")

    def test_require_devnet_and_wallet(self):
        self.assertEqual(classify(example(execution="mainnet"), now=NOW), "HOLD_UNVERIFIED_EXECUTION_MODE")
        self.assertEqual(classify(example(wallet="unavailable"), now=NOW), "SEASON_ACTIVE_WALLET_PENDING")

    def test_active_never_implies_permission_to_trade(self):
        self.assertEqual(classify(example(), now=NOW), "SEASON_ACTIVE_DEPLOYMENT_PENDING")


if __name__ == "__main__":
    unittest.main()
