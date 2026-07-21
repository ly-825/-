import unittest

from app.services.material_formats import (
    format_steel_thickness,
    paper_roll_size,
    paper_sheet_model,
    steel_dimension_sort_key,
    steel_spec_name,
)


class SteelMaterialFormattingTest(unittest.TestCase):
    def test_steel_name_keeps_one_decimal_thickness_first(self) -> None:
        self.assertEqual(steel_spec_name(3, 130, 1270), "3.0×130×1270")
        self.assertEqual(format_steel_thickness(1.84), "1.8")

    def test_steel_sort_key_is_numeric_thickness_width_length(self) -> None:
        values = [(2, 130, 1270), (1.8, 270, 1000), (1.8, 140, 1340)]

        result = sorted(
            values,
            key=lambda value: steel_dimension_sort_key(value[0], value[1], value[2]),
        )

        self.assertEqual(
            result,
            [(1.8, 140, 1340), (1.8, 270, 1000), (2, 130, 1270)],
        )

    def test_paper_sizes_put_thickness_first(self) -> None:
        self.assertEqual(paper_roll_size(0.5, 80, 120), "0.5×80×120")
        self.assertEqual(paper_sheet_model(0.5, 400, 400), "0.5×400×400")


if __name__ == "__main__":
    unittest.main()
