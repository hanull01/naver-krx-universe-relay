import unittest

from tools.market_universe_reconcile import (
    classify_status_candidate, verified_lifecycle_events,
)


class MarketStatusAdapterTests(unittest.TestCase):
    def test_planned_merger_is_unverified(self):
        result = classify_status_candidate(
            code="028080", delta_type="DELISTING",
            evidence={"sourceVerified": True, "planned": True,
                      "effectiveDate": "2026-10-01",
                      "sourceUrl": "https://kind.krx.co.kr/example"})
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(verified_lifecycle_events([result]), [])

    def test_verified_delisting_becomes_existing_event_contract(self):
        result = classify_status_candidate(
            code="469900", delta_type="DELISTING",
            evidence={"sourceVerified": True, "effectiveDate": "2026-10-13",
                      "publishedAt": "2026-10-13T09:00:00+09:00",
                      "sourceUrl": "https://kind.krx.co.kr/example",
                      "changeReason": "official decision"})
        events = verified_lifecycle_events([result])
        self.assertEqual(result["status"], "VERIFIED_DELISTED")
        self.assertEqual(events[0]["type"], "DELISTING")
        self.assertTrue(events[0]["sourceVerified"])

    def test_missing_collection_is_not_delisting(self):
        result = classify_status_candidate(code="028080", delta_type="DELISTING",
                                            collection_missing=True)
        self.assertEqual(result["status"], "COLLECTION_MISSING")
        self.assertEqual(verified_lifecycle_events([result]), [])

    def test_unverified_and_suspension_never_create_unrelated_events(self):
        unknown = classify_status_candidate(code="1", delta_type="SUSPENSION",
                                             evidence={"sourceVerified": False})
        self.assertEqual(unknown["status"], "UNVERIFIED")
        verified = classify_status_candidate(
            code="2", delta_type="SUSPENSION",
            evidence={"sourceVerified": True, "effectiveDate": "2026-10-20"})
        self.assertEqual(verified["status"], "VERIFIED_SUSPENDED")
        self.assertEqual(verified_lifecycle_events([verified]), [])


if __name__ == "__main__":
    unittest.main()
