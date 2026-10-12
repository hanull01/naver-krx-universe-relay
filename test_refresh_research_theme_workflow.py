import unittest
from pathlib import Path


class RefreshResearchThemeWorkflowTests(unittest.TestCase):
    def test_pc_theme_stage_is_optional_and_isolated(self):
        text = (Path(__file__).parent / '.github/workflows/refresh-research.yml').read_text(encoding='utf-8')
        stage = text[text.index('id: pc_theme_rankings'):text.index('- name: Run tests')]
        self.assertIn('continue-on-error: true', stage)
        self.assertIn('tools/collect_pc_theme_research.py', stage)
        self.assertIn('data/research/theme-ranking', stage)
        self.assertNotIn('MARKET_DATA_READY', stage)
        self.assertNotIn('daily_report.py', stage)


if __name__ == '__main__':
    unittest.main()
