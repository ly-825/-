import unittest

from app.services.search_context import build_parameter_summary, keyword_parameter_matches


class SearchContextTest(unittest.TestCase):
    def test_matched_parameters_lead_and_defaults_are_deduplicated(self) -> None:
        result = build_parameter_summary(
            [("总成品厚度", "3"), ("材质", "65Mn")],
            [("总成品厚度", "3"), ("钢板厚度", "1.2"), ("材质", "65Mn")],
        )

        self.assertEqual(
            result,
            [
                ("总成品厚度", "3", True),
                ("材质", "65Mn", True),
                ("钢板厚度", "1.2", False),
            ],
        )

    def test_empty_values_are_removed_and_limit_is_respected(self) -> None:
        result = build_parameter_summary(
            [],
            [("材质", ""), ("厚度", "3"), ("长度", "1000")],
            limit=1,
        )

        self.assertEqual(result, [("厚度", "3", False)])

    def test_keyword_matching_returns_actual_matching_fields(self) -> None:
        result = keyword_parameter_matches(
            "tnx1",
            [("型号", "TNX10.0A"), ("材质", "65Mn"), ("备注", "样品")],
        )

        self.assertEqual(result, [("型号", "TNX10.0A")])


if __name__ == "__main__":
    unittest.main()
