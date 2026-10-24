import unittest

from tools.trading_session_observation import build_observation_diagnostic


class TradingSessionObservationTests(unittest.TestCase):
    def test_matching_local_dates_are_verified(self):
        result = build_observation_diagnostic("20261023", {"A": "2026-10-23", "B": "20261023"})
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["targetDate"], "20261023")
        self.assertEqual(result["matchingCount"], 2)
        self.assertEqual(result["observedDateCounts"], {"20261023": 2})

    def test_mismatch_is_unverified(self):
        result = build_observation_diagnostic("20261023", {"A": "20261023", "B": "20261022"})
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["mismatchCodes"], ["B"])
        self.assertEqual(result["reason"], "LOCAL_DATE_MISMATCH")

    def test_empty_observations_are_unverified(self):
        result = build_observation_diagnostic("20261023", {})
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["reason"], "NO_LOCAL_DATE_OBSERVATIONS")

    def test_invalid_date_rejected(self):
        with self.assertRaises(ValueError):
            build_observation_diagnostic("20260230", {"A": "20260230"})


if __name__ == "__main__":
    unittest.main()
