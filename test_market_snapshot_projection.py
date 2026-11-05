import unittest

from tools.market_snapshot_projection import build_projection


def stock(code="005930", name="삼성전자", **extra):
    row = {"code": code, "name": name, "price": 100, "changeRate": 2.1, "volume": 1000, "tradingValue": 200000}
    row.update(extra)
    return row


class MarketSnapshotProjectionTests(unittest.TestCase):
    def test_projection_keeps_only_compact_report_fields(self):
        payload = build_projection(
            rankings={
                "status": "OK",
                "generatedAt": "2026-11-05T09:30:00+09:00",
                "rankings": [{"name": "gainers", "status": "OK", "rows": [stock(source={"huge": "x" * 10000}) for _ in range(8)]}],
            },
            investor_flow={"status": "OK", "investors": []},
            industries={"status": "OK", "count": 1, "industries": [{"id": "1", "name": "반도체", "changeRate": 3.2}]},
            membership={"status": "OK", "industryCount": 1, "membershipCount": 20},
        )
        rows = payload["rankings"]["items"]["gainers"]["rows"]
        self.assertEqual(len(rows), 5)
        self.assertNotIn("source", rows[0])
        self.assertEqual(payload["status"], "OK")

    def test_investor_flow_is_exposed_for_day_and_periods(self):
        entry = {"investorType": "FOREIGNER", "periodType": "DAY", "status": "OK", "buy": [stock(accTradeAmount=123)], "sell": []}
        payload = build_projection(
            rankings={"status": "OK", "rankings": []},
            investor_flow={"status": "OK", "investors": [entry], "multiPeriod": {"periods": {"WEEK": {"status": "OK", "investors": [entry]}}}},
            industries={"status": "OK", "industries": []},
            membership={"status": "OK"},
        )
        self.assertEqual(payload["investorFlow"]["periods"]["DAY"][0]["buy"][0]["amount"], 123)
        self.assertIn("WEEK", payload["investorFlow"]["periods"])

    def test_failed_source_is_unavailable_and_not_silent_success(self):
        payload = build_projection(
            rankings={"status": "ERROR"},
            investor_flow={"status": "ERROR"},
            industries={"status": "ERROR"},
            membership={"status": "ERROR"},
        )
        self.assertEqual(payload["status"], "UNAVAILABLE")
        self.assertEqual(payload["rankings"]["status"], "ERROR")
        self.assertEqual(payload["investorFlow"]["periods"], {"DAY": []})


if __name__ == "__main__":
    unittest.main()
