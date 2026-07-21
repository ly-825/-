from decimal import Decimal, ROUND_HALF_UP

from app.services.drawing_search import natural_sort_key


def _decimal(value: float | Decimal | int) -> Decimal:
    return Decimal(str(value))


def normalize_steel_thickness(value: float | Decimal) -> float:
    return float(_decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def format_number(value: float | Decimal | int | None) -> str:
    if value is None:
        return "-"
    return format(_decimal(value).normalize(), "f")


def format_steel_thickness(value: float | Decimal) -> str:
    return f"{normalize_steel_thickness(value):.1f}"


def steel_spec_name(thickness: float, width: float, length: float) -> str:
    return f"{format_steel_thickness(thickness)}×{format_number(width)}×{format_number(length)}"


def steel_dimension_sort_key(
    thickness: float | None,
    width: float | None,
    length: float | None,
    material: str = "",
) -> tuple:
    return (
        float("inf") if thickness is None else normalize_steel_thickness(thickness),
        float("inf") if width is None else width,
        float("inf") if length is None else length,
        natural_sort_key(material),
    )


def paper_roll_size(thickness: float, inner_diameter: float, outer_diameter: float) -> str:
    return "×".join(format_number(value) for value in (thickness, inner_diameter, outer_diameter))


def paper_sheet_model(thickness: float, length: float, width: float) -> str:
    return "×".join(format_number(value) for value in (thickness, length, width))
