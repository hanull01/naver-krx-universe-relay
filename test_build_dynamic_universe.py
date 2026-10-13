import unittest

from tools.build_dynamic_universe import usable_industry_rows


class IndustryEvidenceTests(unittest.TestCase):
    def row(self, **changes):
        row = {
            "id": "261", "memberCount": 10, "featureValidMemberCount": 10,
            "closeValidMemberCount": 10, "advancePct": 60.0,
            "relativeStrengthVsMarket": 1.0, "pctAboveMA20": 60.0,
            "pctVolumeAbove20DayAverage": 70.0, "leaders": [],
        }
        row.update(changes)
        return row

    def test_partial_quality_with_valid_rows_is_not_full(self):
        rows, status = usable_industry_rows({
            "quality": {"sourceStatus": "PARTIAL", "industryEvidenceUsable": "UNUSABLE"},
            "industries": [self.row()],
        })
        self.assertEqual(status, "PARTIAL_WITH_COVERAGE")
        self.assertEqual(len(rows), 1)

    def test_incomplete_rows_are_rejected(self):
        rows, status = usable_industry_rows({
            "quality": {"sourceStatus": "PARTIAL", "industryEvidenceUsable": "UNUSABLE"},
            "industries": [self.row(closeValidMemberCount=9), self.row(featureValidMemberCount=0)],
        })
        self.assertEqual(status, "UNUSABLE")
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
