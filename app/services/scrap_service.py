from sqlalchemy.orm import Session

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
    )
    db.add(inventory)
    db.flush()
    record = ScrapGenerationRecord(
        source_product_code=drawing.product_code,
        source_inventory_id=source_inventory.id,
        scrap_inventory_id=inventory.id,
        theoretical_size=theoretical_size,
        actual_size=drawing.expected_scrap_size,
        operator_name=operator_name,
    )
    db.add(record)
    return inventory
