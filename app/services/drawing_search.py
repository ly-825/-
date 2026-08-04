import re

from sqlalchemy import and_, or_

from app.models import ProductDrawing


TOOTH_TYPES = ("IT", "IL", "IR", "OT", "OL", "OR")


def natural_sort_key(value: object) -> tuple:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", str(value or ""))
    )


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


def _optional_float(value: str) -> float | None:
    try:
        return float(value) if value.strip() else None
    except (AttributeError, ValueError):
        return None


def float_between_filter(column, value: float, tolerance: float = 0.001):
    return column.between(value - tolerance, value + tolerance)


def apply_drawing_filters(
    query,
    q: str = "",
    product_category: str = "",
    material: str = "",
    thickness: str = "",
    product_thickness: str = "",
    plate_thickness: str = "",
    outer_diameter: str = "",
    inner_diameter: str = "",
    teeth_count: str = "",
    module: str = "",
    pressure_angle: str = "",
    common_normal_length: str = "",
    pin_diameter: str = "",
    pin_span: str = "",
):
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            (ProductDrawing.product_code.ilike(like))
            | (ProductDrawing.product_name.ilike(like))
            | (ProductDrawing.product_category.ilike(like))
            | (ProductDrawing.remark.ilike(like))
            | (ProductDrawing.material.ilike(like))
            | (ProductDrawing.tooth_type.ilike(like))
            | (ProductDrawing.teeth_count_text.ilike(like))
            | (ProductDrawing.module_text.ilike(like))
            | (ProductDrawing.common_normal_length_text.ilike(like))
            | tooth_search_filter(keyword)
        )
    if product_category.strip():
        query = query.filter(
            ProductDrawing.product_category.ilike(f"%{product_category.strip()}%")
        )
    if material.strip():
        query = query.filter(ProductDrawing.material.ilike(f"%{material.strip()}%"))
    thickness_value = _optional_float(thickness)
    if thickness_value is not None:
        query = query.filter(
            float_between_filter(ProductDrawing.thickness, thickness_value)
            | float_between_filter(ProductDrawing.product_thickness, thickness_value)
            | float_between_filter(ProductDrawing.plate_thickness, thickness_value)
        )
    product_thickness_value = _optional_float(product_thickness)
    if product_thickness_value is not None:
        query = query.filter(
            float_between_filter(
                ProductDrawing.product_thickness, product_thickness_value
            )
        )
    plate_thickness_value = _optional_float(plate_thickness)
    if plate_thickness_value is not None:
        query = query.filter(
            float_between_filter(ProductDrawing.plate_thickness, plate_thickness_value)
        )
    outer_value = _optional_float(outer_diameter)
    if outer_value is not None:
        query = query.filter(
            float_between_filter(ProductDrawing.max_outer_diameter, outer_value)
        )
    inner_value = _optional_float(inner_diameter)
    if inner_value is not None:
        query = query.filter(
            float_between_filter(ProductDrawing.min_inner_diameter, inner_value)
        )
    if teeth_count.strip():
        query = query.filter(tooth_search_filter(teeth_count.strip()))
    module_text = module.strip()
    if module_text:
        module_value = _optional_float(module_text)
        like = f"%{module_text}%"
        if module_value is not None:
            query = query.filter(
                float_between_filter(ProductDrawing.module, module_value)
                | ProductDrawing.module_text.ilike(like)
            )
        else:
            query = query.filter(ProductDrawing.module_text.ilike(like))
    pressure_angle_value = _optional_float(pressure_angle)
    if pressure_angle_value is not None:
        query = query.filter(
            float_between_filter(ProductDrawing.pressure_angle, pressure_angle_value)
        )
    common_normal_text = common_normal_length.strip()
    if common_normal_text:
        common_normal_value = _optional_float(common_normal_text)
        like = f"%{common_normal_text}%"
        if common_normal_value is not None:
            query = query.filter(
                float_between_filter(
                    ProductDrawing.common_normal_length, common_normal_value
                )
                | ProductDrawing.common_normal_length_text.ilike(like)
            )
        else:
            query = query.filter(
                ProductDrawing.common_normal_length_text.ilike(like)
            )
    pin_diameter_value = _optional_float(pin_diameter)
    if pin_diameter_value is not None:
        query = query.filter(
            float_between_filter(ProductDrawing.pin_diameter, pin_diameter_value)
        )
    pin_span_value = _optional_float(pin_span)
    if pin_span_value is not None:
        query = query.filter(
            float_between_filter(ProductDrawing.pin_span, pin_span_value)
        )
    return query
