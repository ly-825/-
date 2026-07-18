import re

from sqlalchemy import and_, or_

from app.models import ProductDrawing


TOOTH_TYPES = ("IT", "IL", "IR", "OT", "OL", "OR")


def natural_sort_key(value: object) -> tuple:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", str(value or ""))
    )


def drawing_sort_key_map() -> dict[str, object]:
    return {
        "product_code": lambda drawing: natural_sort_key(drawing.product_code),
        "product_name": lambda drawing: natural_sort_key(drawing.product_name),
        "product_category": lambda drawing: natural_sort_key(drawing.product_category),
        "material": lambda drawing: natural_sort_key(drawing.material),
        "product_thickness": lambda drawing: drawing.product_thickness,
        "plate_thickness": lambda drawing: drawing.plate_thickness,
        "outer_diameter": lambda drawing: drawing.max_outer_diameter,
        "inner_diameter": lambda drawing: drawing.min_inner_diameter,
        "teeth_count": lambda drawing: natural_sort_key(
            f"{drawing.tooth_type or ''}{drawing.teeth_count_text or drawing.teeth_count or ''}"
        ),
        "module": lambda drawing: natural_sort_key(drawing.module_text or drawing.module),
        "pressure_angle": lambda drawing: drawing.pressure_angle,
        "updated_at": lambda drawing: drawing.updated_at,
    }


def split_tooth_search(value: str) -> tuple[str | None, str]:
    text = re.sub(r"\s+", "", value or "").upper()
    match = re.match(rf"^({'|'.join(TOOTH_TYPES)})(.*)$", text)
    if not match:
        return None, text
    return match.group(1), match.group(2)


def tooth_search_filter(value: str):
    tooth_type, count_text = split_tooth_search(value)
    count_like = f"%{count_text}%"
    count_value = int(count_text) if count_text.isdigit() else None

    count_filter = ProductDrawing.teeth_count_text.ilike(count_like)
    if count_value is not None:
        count_filter = or_(ProductDrawing.teeth_count == count_value, count_filter)

    if tooth_type and count_text:
        return and_(ProductDrawing.tooth_type.ilike(tooth_type), count_filter)
    if tooth_type:
        return ProductDrawing.tooth_type.ilike(tooth_type)
    return or_(count_filter, ProductDrawing.tooth_type.ilike(f"%{count_text}%"))
