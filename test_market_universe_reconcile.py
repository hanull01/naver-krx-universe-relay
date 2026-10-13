import unittest

from tools.market_universe_reconcile import classify_delta, reconcile_verified


def universe(rows):
    return {"assets": [dict(row, assetType="STOCK") for row in rows]}


class UniverseReconcileTests(unittest.TestCase):
    def test_469900_verified_delisting_updates_current_set(self):
        previous = universe([
            {"code": "469900", "name": "폐지종목", "market": "KOSDAQ"},
            {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
        ])
        observed = universe([{ "code": "005930", "name": "삼성전자", "market": "KOSPI"}])
        self.assertEqual(classify_delta(previous, observed)["removed"], ["469900"])
        result, lifecycle = reconcile_verified(
            previous, observed,
            [{"type": "DELISTING", "code": "469900", "effectiveDate": "2026-10-13",
              "sourceVerified": True}],
            target_date="2026-10-13")
        self.assertEqual([row["code"] for row in result["assets"]], ["005930"])
        self.assertEqual(lifecycle["status"], "VERIFIED")

    def test_unverified_change_is_blocked(self):
        previous = universe([{ "code": "005930", "name": "삼성전자", "market": "KOSPI"}])
        observed = universe([{ "code": "005930", "name": "변경", "market": "KOSPI"}])
        with self.assertRaisesRegex(ValueError, "UNVERIFIED"):
            reconcile_verified(previous, observed, [])

    def test_name_change_and_market_transfer_are_distinct(self):
        previous = universe([
            {"code": "1", "name": "구이름", "market": "KOSDAQ"},
            {"code": "2", "name": "이름", "market": "KOSDAQ"},
        ])
        observed = universe([
            {"code": "1", "name": "새이름", "market": "KOSDAQ"},
            {"code": "2", "name": "이름", "market": "KOSPI"},
        ])
        delta = classify_delta(previous, observed)
        self.assertEqual(delta["nameChanged"], ["1"])
        self.assertEqual(delta["marketChanged"], ["2"])

    def test_abnormal_mass_change_is_blocked(self):
        previous = universe([{ "code": str(i), "name": str(i), "market": "KOSPI"} for i in range(10)])
        observed = universe([{ "code": str(i), "name": str(i), "market": "KOSPI"} for i in range(10, 20)])
        with self.assertRaisesRegex(ValueError, "ABNORMAL"):
            reconcile_verified(previous, observed, [])


if __name__ == "__main__":
    unittest.main()
