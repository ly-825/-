from __future__ import annotations

import re

from app.config import settings
from app.models import MaterialInventory, ProductDrawing


def parse_diameter(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.replace("φ", "").replace("Φ", "").strip()
    try:
        return float(cleaned.split()[0])
    except (ValueError, IndexError):
        return None


def effective_drawing_thickness(drawing: ProductDrawing | None) -> float | None:
    if not drawing:
        return None
    return drawing.plate_thickness or drawing.product_thickness or drawing.thickness


def normalize_material(value: str | None) -> str:
    return (value or "").replace(" ", "").upper()


def material_is_compatible(required: str | None, candidate: str | None) -> bool:
    required_text = normalize_material(required)
    candidate_text = normalize_material(candidate)
    if not required_text:
        return True
    if not candidate_text:
        return False
    if required_text in candidate_text or candidate_text in required_text:
        return True
    required_parts = {part for part in re.split(r"[/,，、+＋]", required_text) if part}
    candidate_parts = {part for part in re.split(r"[/,，、+＋]", candidate_text) if part}
    return bool(required_parts & candidate_parts)


def thickness_is_compatible(required: float | None, candidate: float | None) -> bool:
    if required is None:
        return True
    if candidate is None:
        return False
    return abs(candidate - required) <= settings.thickness_tolerance


def drawing_required_diameter(drawing: ProductDrawing | None) -> float | None:
    if not drawing:
        return None
    values = [
        drawing.max_outer_diameter,
        drawing.bounding_length,
        drawing.bounding_width,
    ]
    numeric_values = [value for value in values if value is not None and value > 0]
    if numeric_values:
        return max(numeric_values)
    return parse_diameter(drawing.expected_scrap_size) or drawing.min_inner_diameter


def drawing_required_plate_size(drawing: ProductDrawing | None) -> tuple[float | None, float | None]:
    if not drawing:
        return None, None
    if drawing.bounding_length and drawing.bounding_width:
        return drawing.bounding_length, drawing.bounding_width
    diameter = drawing_required_diameter(drawing)
    if diameter:
        return diameter, diameter
    return None, None


def scrap_required_diameter(drawing: ProductDrawing | None) -> float | None:
    diameter = drawing_required_diameter(drawing)
    return diameter + settings.machining_margin if diameter is not None else None


def scrap_matches_drawing(item: MaterialInventory, drawing: ProductDrawing) -> bool:
    required_diameter = scrap_required_diameter(drawing)
    return (
        material_is_compatible(drawing.material, item.material)
        and thickness_is_compatible(effective_drawing_thickness(drawing), item.thickness)
        and (required_diameter is None or (item.diameter is not None and item.diameter >= required_diameter))
    )


def raw_plate_matches_drawing(item: MaterialInventory, drawing: ProductDrawing) -> bool:
    if not material_is_compatible(drawing.material, item.material):
        return False
    if not thickness_is_compatible(effective_drawing_thickness(drawing), item.thickness):
        return False
    required_length, required_width = drawing_required_plate_size(drawing)
    if required_length is None or required_width is None:
        return True
    if item.length is None or item.width is None:
        return True
    required = sorted([required_length + settings.machining_margin, required_width + settings.machining_margin])
    available = sorted([item.length, item.width])
    return available[0] >= required[0] and available[1] >= required[1]
