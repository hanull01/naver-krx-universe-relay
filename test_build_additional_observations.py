import json
import tempfile
import unittest
from pathlib import Path

from tools.build_additional_observations import build_artifact, main


class AdditionalObservationProducerTests(unittest.TestCase):
    def setUp(self):
        self.config = {"industries": [{"id": "i1", "name": "업종1"}], "themes": [{"id": "t1", "name": "테마1"}]}
        self.industry = {"generatedAt": "2026-11-12T10:00:00+09:00", "sourceDate": "20261112", "observationDate": "20261112", "industries": [{"id": "i1", "name": "업종1", "memberCount": 4, "leaders": [{"code": "1", "name": "A"}]}]}
        self.theme = {"generatedAt": "2026-11-12T10:01:00+09:00", "themes": [{"id": "t1", "name": "테마1", "memberCount": 3, "stocks": [{"code": "2", "name": "B", "changeRate": 2.0}, {"code": "3", "name": "C", "changeRate": 1.0}]}]}

    def build(self, **overrides):
        values = dict(config=self.config, industry_input=self.industry, theme_input=self.theme, observation_date="20261112", run_id="run-1", generated_at="2026-11-12T10:02:00+09:00", config_path="config.json", industry_path="industry.json", theme_path="theme.json")
        values.update(overrides)
        return build_artifact(**values)

    def test_configured_inputs_create_available_rows(self):
        result = self.build()
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["counts"]["available"], 2)
        self.assertEqual(result["themes"][0]["leaders"][0]["code"], "2")

    def test_missing_leader_is_unavailable_without_fallback(self):
        self.industry["industries"][0]["leaders"] = []
        result = self.build()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["industries"][0]["failureReason"], "NO_LEADER_DATA")

    def test_stale_input_is_not_used(self):
        self.industry["sourceDate"] = "20261111"
        result = self.build()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["industries"][0]["status"], "UNAVAILABLE")
        self.assertEqual(result["industries"][0]["failureReason"], "INDUSTRY_INPUT_DATE_MISMATCH")

    def test_no_configuration_is_explicit(self):
        result = self.build(config={"industries": [], "themes": []})
        self.assertEqual(result["status"], "NO_ELIGIBLE_TARGETS")

    def test_missing_configured_input_is_producer_failed(self):
        result = self.build(config={"industries": [], "themes": [{"id": "t1", "name": "테마1"}]}, theme_input=None)
        self.assertEqual(result["status"], "PRODUCER_FAILED")
        self.assertEqual(result["themes"][0]["failureReason"], "THEME_INPUT_MISSING")

    def test_refresh_workflow_runs_and_publishes_the_producer_artifact(self):
        workflow = Path(".github/workflows/refresh.yml").read_text(encoding="utf-8")
        self.assertIn("Build additional observation supply artifact", workflow)
        self.assertIn("python -m tools.build_additional_observations", workflow)
        self.assertIn("data/monitoring/additional-observations.json", workflow)

    def test_cli_writes_status_artifact_without_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(json.dumps({"industries": [], "themes": []}), encoding="utf-8")
            (root / "industry.json").write_text(json.dumps(self.industry), encoding="utf-8")
            (root / "theme.json").write_text(json.dumps(self.theme), encoding="utf-8")
            output = root / "out.json"
            code = main(["--config", str(root / "config.json"), "--industry-input", str(root / "industry.json"), "--theme-input", str(root / "theme.json"), "--output", str(output), "--observation-date", "20261112", "--run-id", "run-cli"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.read_text())["status"], "NO_ELIGIBLE_TARGETS")

    def _auto_inputs(self):
        industries = []
        themes = []
        for index in range(8):
            value = 10.0 - index
            industries.append({
                "id": f"i{index}", "name": f"업종{index}", "memberCount": 10 + index,
                "featureValidMemberCount": 10 + index, "relativeStrengthVsMarket": value,
                "advancePct": 60.0 - index, "leaders": [{"code": f"1{index}", "name": f"종목{index}"}],
                "sourceDate": "20261112", "observationDate": "20261112",
            })
            themes.append({
                "id": f"t{index}", "name": f"테마{index}", "memberCount": 10 + index,
                "featureCount": 10 + index, "coveragePct": 100.0,
                "membershipStatus": "OK", "metrics": {
                    "officialChangeRateRelativeStrength": value, "advancerPct": 60.0 - index,
                }, "stocks": [{"code": f"2{index}", "name": f"테마종목{index}", "changeRate": 5.0}],
                "temporaryCurrentBarDate": "20261112",
            })
        return (
            {"sourceDate": "20261112", "observationDate": "20261112", "generatedAt": "2026-11-12T10:00:00+09:00", "industries": industries},
            {"generatedAt": "2026-11-12T10:00:00+09:00", "themes": themes},
        )

    def test_empty_configuration_auto_selects_industries_and_themes_independently(self):
        industry, theme = self._auto_inputs()
        result = self.build(config={"autoSelect": True, "industries": [], "themes": []}, industry_input=industry, theme_input=theme)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(len(result["industries"]), 3)
        self.assertEqual(len(result["themes"]), 3)
        self.assertTrue(all(row["selectionScore"] is not None for row in result["industries"] + result["themes"]))
        self.assertTrue(set(row["id"] for row in result["industries"]).isdisjoint({f"i{index}" for index in range(5)}))
        self.assertTrue(set(row["id"] for row in result["themes"]).isdisjoint({f"t{index}" for index in range(5)}))

    def test_quality_threshold_excludes_small_or_weak_targets(self):
        industry, theme = self._auto_inputs()
        industry["industries"] = [{
            "id": "small", "name": "작은 업종", "memberCount": 2,
            "featureValidMemberCount": 2, "relativeStrengthVsMarket": 20.0,
            "advancePct": 99.0, "leaders": [{"code": "1", "name": "A"}],
            "sourceDate": "20261112", "observationDate": "20261112",
        }]
        theme["themes"] = []
        result = self.build(config={"autoSelect": True, "policy": {"excludeRelativeStrengthTop5": False}, "industries": [], "themes": []}, industry_input=industry, theme_input=theme)
        self.assertNotIn("small", {row["id"] for row in result["industries"]})
        self.assertIn("MEMBERSHIP_BELOW_MINIMUM", result["selectionPolicy"]["excluded"]["industries"][0]["reason"])

    def test_missing_leader_is_partial_without_inventing_one(self):
        industry, theme = self._auto_inputs()
        industry["industries"][6]["leaders"] = []
        theme["themes"] = []
        result = self.build(config={"autoSelect": True, "industries": [], "themes": []}, industry_input=industry, theme_input=theme)
        selected = next(row for row in result["industries"] if row["id"] == "i6")
        self.assertEqual(selected["status"], "UNAVAILABLE")
        self.assertEqual(selected["failureReason"], "NO_LEADER_DATA")

    def test_date_mismatch_and_empty_inputs_are_explicit(self):
        industry, theme = self._auto_inputs()
        industry["observationDate"] = "20261111"
        theme["themes"] = []
        result = self.build(config={"autoSelect": True, "industries": [], "themes": []}, industry_input=industry, theme_input=theme)
        self.assertEqual(result["status"], "INPUT_UNAVAILABLE")
        self.assertIn("INDUSTRY_INPUT_DATE_MISMATCH", result["failureReason"])


if __name__ == "__main__":
    unittest.main()
