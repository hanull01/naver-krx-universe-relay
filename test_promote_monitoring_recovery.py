import json
import tempfile
import unittest
from pathlib import Path

from tools import promote_monitoring_recovery as promote


class PromotionTests(unittest.TestCase):
    def _fixture(self):
        root = Path(tempfile.mkdtemp())
        (root / "data/monitoring-daily").mkdir(parents=True)
        (root / "data/monitoring-baseline").mkdir(parents=True)
        stage = root / "stage"; (stage / "state").mkdir(parents=True)
        old_stock = {"code": "000001", "dates": ["20261016"], "close": [100], "high": [101], "volume": [10], "validity": [0]}
        new_stock = {"code": "000001", "dates": ["20261016", "20261019"], "close": [100, 101], "high": [101, 102], "volume": [10, 11], "validity": [0, 0]}
        old_kosdaq = {**old_stock, "code": "000002"}; new_kosdaq = {**new_stock, "code": "000002"}
        (root / "data/monitoring-daily/kospi.json").write_text(json.dumps({"asOfDate": "20261016", "stocks": [old_stock]}))
        (root / "data/monitoring-daily/kosdaq.json").write_text(json.dumps({"asOfDate": "20261016", "stocks": [old_kosdaq]}))
        (stage / "state/kospi.json").write_text(json.dumps({"asOfDate": "20261026", "publicationStatus": "FINAL", "stocks": [new_stock]}))
        (stage / "state/kosdaq.json").write_text(json.dumps({"asOfDate": "20261026", "publicationStatus": "FINAL", "stocks": [new_kosdaq]}))
        (root / "data/monitoring-baseline/latest.json").write_text(json.dumps({"asOfDate": "20261016", "historyStatus": "OK"}))
        (root / "data/market-universe").mkdir(parents=True)
        (root / "data/market-universe/all-assets.json").write_text(json.dumps({"assets": [
            {"code": "000001", "assetType": "STOCK", "market": "KOSPI"},
            {"code": "000002", "assetType": "STOCK", "market": "KOSDAQ"}
        ]}))
        baseline = {"asOfDate": "20261026", "historyStatus": "SOURCE_OBSERVED", "authoritativeCount": 2, "count": 2, "coveragePct": 100.0, "stocks": [{"code": "000001"}, {"code": "000002"}], "missingLatestCodes": []}
        (stage / "baseline-final.json").write_text(json.dumps(baseline))
        return root, stage

    def test_default_is_dry_run_and_preserves_production(self):
        root, stage = self._fixture()
        before = (root / "data/monitoring-daily/kospi.json").read_bytes()
        report = promote.promote(root, stage, root / "backup")
        self.assertEqual(report["status"], "PROMOTION_READY")
        self.assertEqual(report["mode"], "DRY_RUN")
        self.assertEqual((root / "data/monitoring-daily/kospi.json").read_bytes(), before)

    def test_duplicate_dates_are_rejected(self):
        root, stage = self._fixture()
        payload = json.loads((stage / "state/kospi.json").read_text())
        payload["stocks"][0]["dates"].append("20261019")
        (stage / "state/kospi.json").write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "duplicate dates"):
            promote.validate_promotion(root, stage)

    def test_apply_rolls_back_all_files_after_second_replace_failure(self):
        root, stage = self._fixture(); backup = root / "backup"
        calls = {"count": 0}
        original = promote.os.replace
        def fail_once(source, destination):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("injected failure")
            return original(source, destination)
        before = {path: path.read_bytes() for path in (root / "data/monitoring-daily/kospi.json", root / "data/monitoring-daily/kosdaq.json", root / "data/monitoring-baseline/latest.json")}
        with self.assertRaisesRegex(OSError, "injected failure"):
            promote.promote(root, stage, backup, apply=True, confirm_production=True, replace_fn=fail_once)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
