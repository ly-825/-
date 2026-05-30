from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import MaterialInventory, OperationLog, ProductDrawing


def compact_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def drawing_snapshot(drawing: ProductDrawing | None) -> dict[str, Any] | None:
    if not drawing:
        return None
    return {
        "id": drawing.id,
        "product_code": drawing.product_code,
        "product_name": drawing.product_name,
        "material": drawing.material,
        "thickness": drawing.thickness,
        "product_thickness": drawing.product_thickness,
        "plate_thickness": drawing.plate_thickness,
        "max_outer_diameter": drawing.max_outer_diameter,
        "min_inner_diameter": drawing.min_inner_diameter,
        "expected_scrap_size": drawing.expected_scrap_size,
        "parse_status": drawing.parse_status,
        "confirmed": drawing.confirmed,
        "version": drawing.version,
        "is_active": drawing.is_active,
        "previous_drawing_id": drawing.previous_drawing_id,
        "replaced_by_id": drawing.replaced_by_id,
        "file_hash": drawing.file_hash,
    }


def inventory_snapshot(item: MaterialInventory | None) -> dict[str, Any] | None:
    if not item:
        return None
    return {
        "id": item.id,
        "material_code": item.material_code,
        "inventory_type": item.inventory_type,
        "material": item.material,
        "thickness": item.thickness,
        "shape": item.shape,
        "diameter": item.diameter,
        "usable_size": item.usable_size,
        "quantity": item.quantity,
        "location": item.location,
        "status": item.status,
        "source_product_code": item.source_product_code,
        "source_drawing_id": item.source_drawing_id,
    }


def record_operation_log(
    db: Session,
    action: str,
    object_type: str,
    object_id: int | None = None,
    operator_name: str | None = None,
    remark: str | None = None,
    before_data: dict[str, Any] | None = None,
    after_data: dict[str, Any] | None = None,
) -> OperationLog:
    log = OperationLog(
        action=action,
        object_type=object_type,
        object_id=object_id,
        operator_name=operator_name or None,
        remark=remark or None,
        before_data=before_data,
        after_data=after_data,
    )
    db.add(log)
    return log
