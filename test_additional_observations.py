import json
import unittest

from tools.additional_observations import build_additional_observation_projection
from tools.build_additional_observations import build_artifact
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
            "producer": "tools/build_additional_observations.py",
            "producerRunId": "run-additional-1",
            "generatedAt": "2026-11-12T10:00:00+09:00",
            "artifactPath": "data/monitoring/additional-observations.json",
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

    def test_available_artifact_preserves_top_level_producer_lineage(self):
        result = build_additional_observation_projection(
            self.config, self.artifact(), observation_date="20261112"
        )
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["producer"], "tools/build_additional_observations.py")
        self.assertEqual(result["producerRunId"], "run-additional-1")

    def test_old_only_artifact_is_rejected_without_fallback(self):
        old = self.artifact()
        old["sourceDate"] = old["observationDate"] = "20261111"
        result = build_additional_observation_projection(self.config, old, observation_date="20261112")
        self.assertEqual(result["status"], "PRODUCER_FAILED")
        self.assertTrue(all(row["failureReason"] == "STALE_ARTIFACT" for row in result["industries"] + result["themes"]))

    def test_missing_row_lineage_is_unavailable_not_promoted(self):
        artifact = self.artifact()
        artifact.pop("producer")
        artifact.pop("artifactPath")
        result = build_additional_observation_projection(self.config, artifact, observation_date="20261112")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertTrue(all(row["status"] == "UNAVAILABLE" for row in result["industries"] + result["themes"]))
        self.assertTrue(all("ADDITIONAL_LINEAGE_MISSING" in row["failureReason"] for row in result["industries"] + result["themes"]))

    def test_renderer_uses_same_contract_projection_for_html_and_markdown(self):
        from test_market_dashboard_renderer import report_fixture

        report = report_fixture()
        artifact = self.artifact()
        artifact.update({
            "producer": "tools/build_additional_observations.py",
            "artifactPath": "data/monitoring/additional-observations.json",
        })
        for index, row in enumerate(artifact["industries"] + artifact["themes"], start=1):
            row.update({
                "producer": artifact["producer"], "producerRunId": "run-additional-1",
                "artifactPath": f"{artifact['artifactPath']}#{row['id']}",
                "selectionRank": index, "selectionReason": "CURRENT_OBSERVATION_RANKING",
            })
        report["reportContract"]["additionalObservations"] = build_additional_observation_projection(
            self.config, artifact, observation_date="20261112"
        )
        markdown_model = build_dashboard_model(report)
        html_model = build_dashboard_model(report)
        self.assertEqual(validate_renderer_models(report, report), [])
        markdown = render_dashboard_markdown(report)
        html = render_dashboard_html(report)
        from tools.market_dashboard_renderer import validate_rendered_outputs
        self.assertEqual(validate_rendered_outputs(markdown, html, markdown_model), [])
        for token in ("추가 업종", "추가 테마", "NAVER_CURRENT_SNAPSHOT", "2026-11-12"):
            self.assertIn(token, markdown)
            self.assertIn(token, html)
        self.assertEqual(markdown_model["additionalObservations"], html_model["additionalObservations"])
        self.assertNotIn("PRODUCER_NOT_CONFIGURED", markdown)
        self.assertNotIn("NOT_YET_RUN", html)

    def test_artifact_without_contract_link_is_not_reported_as_unconfigured(self):
        from test_market_dashboard_renderer import report_fixture

        report = report_fixture()
        contract = report["reportContract"]
        contract.pop("additionalObservations", None)
        contract["sources"] = {"additionalObservations": {
            "status": "AVAILABLE",
            "artifactPath": "data/monitoring/additional-observations.json",
            "sourceDate": "20261112",
        }}
        markdown = render_dashboard_markdown(report)
        html = render_dashboard_html(report)
        self.assertIn("리포트 연결 실패", markdown)
        self.assertIn("리포트 연결 실패", html)
        section = markdown.split("### 추가 관찰 업종·테마", 1)[1].split("#### 별도 산출 진단", 1)[0]
        self.assertNotIn("자동 산출 기능 미연결", section)

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

    def test_auto_selected_rows_preserve_complete_lineage(self):
        industry_input = {
            "sourceDate": "20261112", "observationDate": "20261112",
            "industries": [
                {"id": f"i-{index}", "name": f"업종 {index}", "relativeStrengthVsMarket": float(10 - index),
                 "advancePct": 90, "memberCount": 10, "leaders": [{"code": f"00{index:04d}", "name": f"종목 {index}"}]}
                for index in range(6)
            ],
        }
        theme_input = {
            "sourceDate": "20261112", "observationDate": "20261112",
            "themes": [
                {"id": f"t-{index}", "name": f"테마 {index}", "relativeStrengthVsMarket": float(10 - index),
                 "metrics": {"relativeStrengthVsMarket": float(10 - index), "advancerPct": 90}, "memberCount": 10,
                 "stocks": [{"code": f"11{index:04d}", "name": f"테마종목 {index}", "changeRate": 1.0}]}
                for index in range(6)
            ],
        }
        artifact = build_artifact(
            config={"autoSelect": True, "industries": [], "themes": []},
            industry_input=industry_input,
            theme_input=theme_input,
            observation_date="20261112",
            run_id="lineage-run",
            generated_at="2026-11-12T10:00:00+09:00",
            config_path="config/additional-observations.json",
            industry_path="data/monitoring/latest-industries.json",
            theme_path="data/monitoring/latest-theme-signals.json",
            artifact_path="data/monitoring/additional-observations.json",
        )
        rows = artifact["industries"] + artifact["themes"]
        self.assertEqual(len(rows), 2)
        for row in rows:
            for field in ("source", "sourceDate", "observationDate", "scope", "producer", "artifactPath", "generatedAt", "selectionRank", "selectionReason", "failureReason"):
                self.assertIn(field, row)
            self.assertIn("#", row["artifactPath"])


if __name__ == "__main__":
    unittest.main()
