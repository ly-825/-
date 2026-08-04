from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import MaterialInventory, ProductDrawing
from app.services.drawing_search import apply_drawing_filters
from app.services.material_matching import (
    effective_drawing_thickness,
    raw_plate_matches_drawing,
    scrap_matches_drawing,
)


RECOMMENDATIONS = {
    "product": "建议优先使用成品库存，当前成品数量已满足计划。",
    "scrap": "成品不足，建议优先使用匹配余料安排生产。",
    "raw_plate": "成品和余料不足，当前有匹配板料，可安排板料生产。",
    "purchase": "成品、余料和板料都未匹配到足够材料，建议先采购或入库。",
}


def list_plan_drawings(
    db: Session,
    *,
    q: str = "",
    material: str = "",
    thickness: str = "",
    outer_diameter: str = "",
    inner_diameter: str = "",
    teeth_count: str = "",
) -> list[ProductDrawing]:
    query = db.query(ProductDrawing).filter(
        ProductDrawing.confirmed == 1,
        ProductDrawing.is_active == 1,
    )
    query = apply_drawing_filters(
        query,
        q=q,
        material=material,
        thickness=thickness,
        outer_diameter=outer_diameter,
        inner_diameter=inner_diameter,
        teeth_count=teeth_count,
    )
    return query.order_by(
        ProductDrawing.product_code.asc(), ProductDrawing.version.desc()
    ).all()


def _time_value(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _inventory_batch(item: MaterialInventory) -> dict[str, object]:
    return {
        "id": item.id,
        "material_code": item.material_code,
        "raw_plate_model": item.raw_plate_model,
        "material": item.material,
        "thickness": item.thickness,
        "diameter": item.diameter,
        "length": item.length,
        "width": item.width,
        "usable_size": item.usable_size,
        "quantity": item.quantity,
        "location": item.location,
        "source_product_code": item.source_product_code,
        "created_at": _time_value(item.created_at),
        "updated_at": _time_value(item.updated_at),
    }


def _inventory_result(
    items: list[MaterialInventory], requested_quantity: int, *, any_is_enough: bool = False
) -> dict[str, object]:
    total = sum(item.quantity for item in items)
    return {
        "quantity": total,
        "batch_count": len(items),
        "enough": total > 0 if any_is_enough else total >= requested_quantity,
        "batches": [_inventory_batch(item) for item in items],
    }


def match_plan_materials(
    db: Session,
    *,
    drawing_id: int,
    quantity: int,
) -> dict[str, object]:
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="计划数量必须大于0")
    drawing = db.get(ProductDrawing, drawing_id)
    if not drawing or drawing.confirmed != 1 or drawing.is_active != 1:
        raise HTTPException(status_code=404, detail="已确认图纸不存在")

    product_code = drawing.product_code or ""
    product_items = (
        db.query(MaterialInventory)
        .filter(
            MaterialInventory.inventory_type == "product",
            MaterialInventory.quantity > 0,
        )
        .order_by(MaterialInventory.updated_at.desc(), MaterialInventory.id.desc())
        .all()
    )
    product_items = [
        item
        for item in product_items
        if product_code
        and (
            item.material_code == product_code
            or item.source_product_code == product_code
        )
    ]

    scrap_candidates = (
        db.query(MaterialInventory)
        .filter(
            MaterialInventory.inventory_type == "scrap",
            MaterialInventory.status == "available",
            MaterialInventory.quantity > 0,
        )
        .order_by(MaterialInventory.diameter.asc(), MaterialInventory.created_at.asc())
        .all()
    )
    scrap_items = [
        item for item in scrap_candidates if scrap_matches_drawing(item, drawing)
    ]

    raw_candidates = (
        db.query(MaterialInventory)
        .filter(
            MaterialInventory.inventory_type == "raw_plate",
            MaterialInventory.quantity > 0,
        )
        .order_by(MaterialInventory.created_at.asc(), MaterialInventory.id.asc())
        .all()
    )
    raw_items = [
        item for item in raw_candidates if raw_plate_matches_drawing(item, drawing)
    ]

    product = _inventory_result(product_items, quantity)
    scrap = _inventory_result(scrap_items, quantity)
    raw_plate = _inventory_result(raw_items, quantity, any_is_enough=True)
    if product["enough"]:
        recommendation_code = "product"
    elif scrap["enough"]:
        recommendation_code = "scrap"
    elif raw_plate["enough"]:
        recommendation_code = "raw_plate"
    else:
        recommendation_code = "purchase"

    return {
        "drawing": {
            "id": drawing.id,
            "product_code": drawing.product_code,
            "product_name": drawing.product_name,
            "product_category": drawing.product_category,
            "material": drawing.material,
            "thickness": effective_drawing_thickness(drawing),
            "outer_diameter": drawing.max_outer_diameter,
            "inner_diameter": drawing.min_inner_diameter,
            "teeth_count": drawing.teeth_count,
            "version": drawing.version,
        },
        "requested_quantity": quantity,
        "product": product,
        "scrap": scrap,
        "raw_plate": raw_plate,
        "recommendation_code": recommendation_code,
        "recommendation": RECOMMENDATIONS[recommendation_code],
    }
