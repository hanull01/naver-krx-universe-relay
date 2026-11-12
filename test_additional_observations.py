import json
import unittest

from tools.additional_observations import build_additional_observation_projection
from tools.market_dashboard_renderer import (
    build_dashboard_model,
    render_dashboard_html,
    render_dashboard_markdown,
    validate_renderer_models,
)


class AdditionalObservationProjectionTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "producer": "NAVER_CURRENT_SNAPSHOT_ADDITIONAL_OBSERVATION",
            "industries": [{"id": "industry-1", "name": "추가 업종"}],
            "themes": [{"id": "theme-1", "name": "추가 테마"}],
        }

    def artifact(self, *, status="AVAILABLE", industry_leaders=True, theme_leaders=True):
        return {
            "schemaVersion": "additional-observations-artifact-v1",
            "status": status,
            "source": "NAVER_CURRENT_SNAPSHOT",
            "sourceDate": "20261112",
            "observationDate": "20261112",
            "scope": "STOCK_ONLY",
            "producerRunId": "run-additional-1",
            "generatedAt": "2026-11-12T10:00:00+09:00",
            "industries": [{
                "id": "industry-1", "name": "추가 업종", "membershipCount": 12,
                "leaders": ([{"code": "005930", "name": "삼성전자"}] if industry_leaders else []),
                "status": "AVAILABLE",
            }],
            "themes": [{
                "id": "theme-1", "name": "추가 테마", "membershipCount": 5,
                "leaders": ([{"code": "000660", "name": "SK하이닉스"}] if theme_leaders else []),
                "status": "AVAILABLE",
            }],
        }

    def test_no_config_is_explicitly_not_configured(self):
        result = build_additional_observation_projection({}, None, observation_date="20261112")
        self.assertEqual(result["status"], "INPUT_UNAVAILABLE")
        self.assertEqual(result["counts"]["configured"], 0)

    def test_config_without_artifact_is_not_yet_run(self):
        result = build_additional_observation_projection(self.config, None, observation_date="20261112")
        self.assertEqual(result["status"], "NOT_YET_RUN")
        self.assertEqual({row["status"] for row in result["industries"] + result["themes"]}, {"NOT_YET_RUN"})

    def test_failed_producer_is_not_promoted(self):
        artifact = {"status": "FAILED", "failureReason": "NAVER_REQUEST_FAILED", "sourceDate": "20261112", "observationDate": "20261112"}
        result = build_additional_observation_projection(self.config, artifact, observation_date="20261112")
        self.assertEqual(result["status"], "PRODUCER_FAILED")
        self.assertTrue(all(row["status"] == "PRODUCER_FAILED" for row in result["industries"] + result["themes"]))

    def test_partial_and_all_success_are_distinguished(self):
        partial = self.artifact(theme_leaders=False)
        result = build_additional_observation_projection(self.config, partial, observation_date="20261112")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["counts"]["available"], 1)
        self.assertEqual(result["themes"][0]["failureReason"], "NO_LEADER_DATA")
        complete = build_additional_observation_projection(self.config, self.artifact(), observation_date="20261112")
        self.assertEqual(complete["status"], "AVAILABLE")
        self.assertEqual(complete["counts"]["available"], 2)

    def test_old_only_artifact_is_rejected_without_fallback(self):
        old = self.artifact()
        old["sourceDate"] = old["observationDate"] = "20261111"
        result = build_additional_observation_projection(self.config, old, observation_date="20261112")
        self.assertEqual(result["status"], "PRODUCER_FAILED")
        self.assertTrue(all(row["failureReason"] == "STALE_ARTIFACT" for row in result["industries"] + result["themes"]))

    def test_renderer_uses_same_contract_projection_for_html_and_markdown(self):
        from test_market_dashboard_renderer import report_fixture

        report = report_fixture()
        report["reportContract"]["additionalObservations"] = build_additional_observation_projection(
            self.config, self.artifact(), observation_date="20261112"
        )
        markdown_model = build_dashboard_model(report)
        html_model = build_dashboard_model(report)
        self.assertEqual(validate_renderer_models(report, report), [])
        markdown = render_dashboard_markdown(report)
        html = render_dashboard_html(report)
        for token in ("추가 업종", "추가 테마", "NAVER_CURRENT_SNAPSHOT", "2026-11-12"):
            self.assertIn(token, markdown)
            self.assertIn(token, html)
        self.assertEqual(markdown_model["additionalObservations"], html_model["additionalObservations"])
        self.assertNotIn("PRODUCER_NOT_CONFIGURED", markdown)
        self.assertNotIn("NOT_YET_RUN", html)

    def test_projection_is_compact_for_contract_transport(self):
        result = build_additional_observation_projection(
            {"industries": [{"id": f"i-{index}"} for index in range(1000)], "themes": []},
            self.artifact(),
            observation_date="20261112",
        )
        self.assertLess(len(json.dumps(result, ensure_ascii=False).encode()), 900_000)

    def test_auto_selected_artifact_projection_preserves_selection_lineage(self):
        artifact = self.artifact()
        artifact["industries"][0].update({
            "selectionRank": 1,
            "selectionScore": 88.5,
            "selectionReason": "CURRENT_OBSERVATION_RANKING",
            "selectionEvidence": {"relativeStrength": 2.1, "advancePct": 75.0, "memberCount": 12, "sourceDate": "20261112", "observationDate": "20261112", "source": "NAVER_CURRENT_SNAPSHOT", "scope": "STOCK_ONLY"},
        })
        result = build_additional_observation_projection(
            {"autoSelect": True, "industries": [], "themes": []}, artifact,
            observation_date="20261112",
        )
        row = result["industries"][0]
        self.assertEqual(row["selectionRank"], 1)
        self.assertEqual(row["selectionScore"], 88.5)
        self.assertEqual(row["selectionEvidence"]["sourceDate"], "20261112")


if __name__ == "__main__":
    unittest.main()
