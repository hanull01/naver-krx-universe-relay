import pathlib
import unittest


WORKFLOW = pathlib.Path(__file__).parent / ".github/workflows/krx-m10-fanout.yml"


class KRXM10FanoutContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_run_name_and_concurrency_include_publish_state(self):
        self.assertIn("publish-${{ inputs.publish || github.event.client_payload.publish || 'false' }}", self.text)
        self.assertIn("-${{ inputs.publish || github.event.client_payload.publish || 'false' }}", self.text)

    def test_execution_id_is_generation_date_publish(self):
        self.assertIn("execution_id=${REQUEST_GENERATION}_${REQUEST_DATE}_${REQUEST_PUBLISH}", self.text)
        self.assertIn("steps.request.outputs.execution_id", self.text)

    def test_duplicate_key_is_publish_aware(self):
        self.assertIn("EXPECTED_TITLE: KRX M10 fan-out ${{ steps.request.outputs.generation_id }} ${{ steps.request.outputs.expected_date }} publish-${{ steps.request.outputs.publish }}", self.text)
        self.assertIn("LEGACY_TITLE:", self.text)
        self.assertIn('publish == "false"', self.text)
        self.assertIn("same-state", self.text.lower()) if "same-state" in self.text.lower() else self.assertIn("PUBLISH_STATE", self.text)

    def test_publish_false_safety_contract_remains(self):
        self.assertIn("-f publish=\"$PUBLISH\"", self.text)
        self.assertIn("Report NO_PUBLISH validation result", self.text)
        self.assertIn("PUBLISH_FALSE_COMMIT_STEP_RAN", self.text)

    def test_resolver_and_sequential_children_remain(self):
        self.assertIn("resolve-krx-generation", self.text)
        self.assertIn("targets=(refresh.yml refresh-monitoring-daily.yml refresh-market-snapshots.yml refresh-research.yml daily-report.yml)", self.text)
        self.assertIn("gh run watch", self.text)


if __name__ == "__main__":
    unittest.main()
