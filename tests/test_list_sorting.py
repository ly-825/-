import unittest

from app.services.drawing_search import natural_sort_key
from app.services.list_sorting import sort_records


class ListSortingTest(unittest.TestCase):
    def test_sorts_natural_text_in_both_directions(self) -> None:
        rows = [{"code": "TNX10"}, {"code": "TNX2"}, {"code": "TNX1"}]

        ascending, sort_by, sort_dir = sort_records(
            rows,
            "code",
            "asc",
            {"code": lambda row: natural_sort_key(row["code"])},
        )
        descending, _, _ = sort_records(
            rows,
            "code",
            "desc",
            {"code": lambda row: natural_sort_key(row["code"])},
        )

        self.assertEqual([row["code"] for row in ascending], ["TNX1", "TNX2", "TNX10"])
        self.assertEqual([row["code"] for row in descending], ["TNX10", "TNX2", "TNX1"])
        self.assertEqual((sort_by, sort_dir), ("code", "asc"))

    def test_sorts_numbers_and_keeps_empty_values_last(self) -> None:
        rows = [{"value": None}, {"value": 10}, {"value": 2}]

        result, _, _ = sort_records(
            rows,
            "value",
            "desc",
            {"value": lambda row: row["value"]},
        )

        self.assertEqual([row["value"] for row in result], [10, 2, None])

    def test_invalid_sort_returns_original_order_and_empty_selection(self) -> None:
        rows = [{"value": 2}, {"value": 1}]

        result, sort_by, sort_dir = sort_records(
            rows,
            "__bad__",
            "sideways",
            {"value": lambda row: row["value"]},
        )

        self.assertEqual(result, rows)
        self.assertEqual((sort_by, sort_dir), ("", ""))


if __name__ == "__main__":
    unittest.main()
