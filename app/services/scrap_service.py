from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.models import MaterialInventory, ProductDrawing, ScrapGenerationRecord


def parse_size_diameter(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = value.replace("φ", "").replace("Φ", "").strip()
    try:
        return float(cleaned.split()[0])
    except (ValueError, IndexError):
        return None


def create_center_scrap_from_drawing(
    drawing: ProductDrawing,
    source_inventory: MaterialInventory,
    operator_name: str | None,
    db: Session,
    quantity: int = 1,
) -> MaterialInventory | None:
    actual_diameter = parse_size_diameter(drawing.expected_scrap_size)
    if not actual_diameter:
        return None
    theoretical_size = f"φ{drawing.min_inner_diameter:g}" if drawing.min_inner_diameter else drawing.expected_scrap_size
    inventory = MaterialInventory(
        inventory_type="scrap",
        material=source_inventory.material,
        thickness=source_inventory.thickness,
        shape="circle",
        diameter=actual_diameter,
        usable_size=drawing.expected_scrap_size,
        quantity=quantity,
        location="待入库",
        status="pending",
        source_product_code=drawing.product_code,
        source_drawing_id=drawing.id,
    )
    db.add(inventory)
    db.flush()
    record = ScrapGenerationRecord(
        source_product_code=drawing.product_code,
        source_drawing_id=drawing.id,
        source_inventory_id=source_inventory.id,
        scrap_inventory_id=inventory.id,
        theoretical_size=theoretical_size,
        actual_size=drawing.expected_scrap_size,
        operator_name=operator_name,
    )
    db.add(record)
    return inventory


def scrap_location_label(item: MaterialInventory | None) -> str:
    if not item:
        return "-"
    if item.status == "available" and item.location in ("待入库", "未入库"):
        return "未设置库位"
    return item.location or "-"


def validate_scrap_for_drawing(item: MaterialInventory, drawing: ProductDrawing) -> None:
    required_diameter = parse_size_diameter(drawing.expected_scrap_size) or drawing.min_inner_diameter or drawing.max_outer_diameter
    required_thickness = drawing.plate_thickness or drawing.product_thickness or drawing.thickness
    if drawing.material:
        drawing_material = drawing.material.replace(" ", "")
        item_material = item.material.replace(" ", "")
        if drawing_material not in item_material and item_material not in drawing_material:
            raise HTTPException(status_code=400, detail="余料材质不满足图纸要求")
    if required_thickness is not None and abs(item.thickness - required_thickness) > settings.thickness_tolerance:
        raise HTTPException(status_code=400, detail="余料厚度不满足图纸要求")
    if required_diameter is not None and (item.diameter is None or item.diameter < required_diameter + settings.machining_margin):
        raise HTTPException(status_code=400, detail="余料尺寸不满足图纸和加工余量要求")


def find_scrap_batches_for_outbound(
    scrap_group_key: str,
    db: Session,
    drawing: ProductDrawing | None = None,
) -> list[MaterialInventory]:
    parts = scrap_group_key.split("||")
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail="余料规格参数错误")
    material_value, thickness_text, usable_size_value, location_value = parts
    thickness_value = parse_size_diameter(thickness_text)
    query = db.query(MaterialInventory).filter(
        MaterialInventory.inventory_type == "scrap",
        MaterialInventory.status == "available",
        MaterialInventory.quantity > 0,
        MaterialInventory.material == material_value,
    )
    query = query.filter(MaterialInventory.usable_size.is_(None)) if usable_size_value == "-" else query.filter(MaterialInventory.usable_size == usable_size_value)
    batches = query.order_by(MaterialInventory.created_at.asc()).all()
    if thickness_value is not None:
        batches = [item for item in batches if item.thickness == thickness_value]
    batches = [item for item in batches if scrap_location_label(item) == location_value]
    if drawing:
        for item in batches:
            validate_scrap_for_drawing(item, drawing)
    return batches
