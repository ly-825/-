from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import InventoryTransactionRecord, MaterialInventory, ProductDrawing, ScrapGenerationRecord
from app.services.scrap_service import create_center_scrap_from_drawing

_inventory_lock = RLock()


@contextmanager
def inventory_write_lock():
    with _inventory_lock:
        yield


def drawing_has_inventory_references(drawing_id: int, db: Session) -> bool:
    has_inventory = db.query(MaterialInventory.id).filter(MaterialInventory.source_drawing_id == drawing_id).first() is not None
    has_scrap_generation = db.query(ScrapGenerationRecord.id).filter(ScrapGenerationRecord.source_drawing_id == drawing_id).first() is not None
    return has_inventory or has_scrap_generation


def ensure_drawing_can_be_changed(drawing: ProductDrawing, db: Session) -> None:
    if drawing_has_inventory_references(drawing.id, db):
        raise HTTPException(status_code=400, detail="该图纸已产生库存或余料记录，不能直接修改、删除或重新识别")


def reject_direct_inventory_write() -> None:
    raise HTTPException(status_code=403, detail="库存写入必须通过已确认图纸入库、产品出库、余料确认或余料出库流程")


@dataclass
class ProductInboundResult:
    item: MaterialInventory
    before_total_quantity: int
    after_total_quantity: int
    duplicated_request: bool = False


def product_inbound_from_drawing(
    drawing: ProductDrawing,
    quantity: int,
    location: str | None,
    operator_name: str | None,
    db: Session,
    idempotency_key: str | None = None,
) -> ProductInboundResult:
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="入库数量必须大于0")
    if not drawing or drawing.confirmed != 1 or drawing.is_active != 1:
        raise HTTPException(status_code=404, detail="已确认图纸不存在")
    if idempotency_key:
        existing_record = db.query(InventoryTransactionRecord).filter(InventoryTransactionRecord.idempotency_key == idempotency_key).first()
        if existing_record:
            existing_item = db.get(MaterialInventory, existing_record.inventory_id)
            if existing_item:
                return ProductInboundResult(
                    item=existing_item,
                    before_total_quantity=existing_record.before_quantity,
                    after_total_quantity=existing_record.after_quantity,
                    duplicated_request=True,
                )
            raise HTTPException(status_code=409, detail="重复请求对应的库存记录不存在")

    thickness = drawing.plate_thickness or drawing.product_thickness or drawing.thickness
    if not drawing.product_code or not drawing.material or thickness is None:
        raise HTTPException(status_code=400, detail="图纸缺少产品编号、材质或厚度，不能入库")

    before_total_quantity = sum(
        item.quantity
        for item in db.query(MaterialInventory)
        .filter(MaterialInventory.inventory_type == "product", MaterialInventory.material_code == drawing.product_code)
        .all()
    )
    location_value = location.strip() if location else None
    existing_query = db.query(MaterialInventory).filter(
        MaterialInventory.inventory_type == "product",
        MaterialInventory.material_code == drawing.product_code,
    )
    if location_value is None:
        existing_query = existing_query.filter(MaterialInventory.location.is_(None))
    else:
        existing_query = existing_query.filter(MaterialInventory.location == location_value)
    item = existing_query.order_by(MaterialInventory.created_at.asc()).first()
    if item:
        item_before_quantity = item.quantity
        item.quantity += quantity
        item.status = "available"
        item.material = drawing.material
        item.thickness = thickness
        item.diameter = drawing.max_outer_diameter
        item.length = drawing.max_outer_diameter
        item.width = drawing.max_outer_diameter
        if drawing.max_outer_diameter:
            item.usable_size = f"φ{drawing.max_outer_diameter:g}"
        item.source_product_code = drawing.product_code
        item.source_drawing_id = drawing.id
    else:
        item_before_quantity = 0
        item = MaterialInventory(
            material_code=drawing.product_code,
            inventory_type="product",
            material=drawing.material,
            thickness=thickness,
            shape="circle",
            diameter=drawing.max_outer_diameter,
            length=drawing.max_outer_diameter,
            width=drawing.max_outer_diameter,
            quantity=quantity,
            location=location_value,
            usable_size=f"φ{drawing.max_outer_diameter:g}" if drawing.max_outer_diameter else None,
            status="available",
            source_product_code=drawing.product_code,
            source_drawing_id=drawing.id,
        )
        db.add(item)
    db.flush()
    db.add(
        InventoryTransactionRecord(
            inventory_id=item.id,
            transaction_type="in",
            quantity=quantity,
            before_quantity=item_before_quantity,
            after_quantity=item.quantity,
            idempotency_key=idempotency_key,
            operator_name=operator_name or None,
            remark="产品入库",
        )
    )

    # Core rule: every product inbound creates the same quantity of pending center scraps.
    create_center_scrap_from_drawing(drawing, item, operator_name or None, db, quantity=quantity)
    return ProductInboundResult(
        item=item,
        before_total_quantity=before_total_quantity,
        after_total_quantity=before_total_quantity + quantity,
    )


def adjust_inventory_quantity(
    item: MaterialInventory,
    transaction_type: str,
    quantity: int,
    operator_name: str | None,
    remark: str | None,
    db: Session,
) -> InventoryTransactionRecord:
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="数量必须大于0")
    before_quantity = item.quantity
    if transaction_type == "in":
        after_quantity = before_quantity + quantity
    elif transaction_type == "out":
        if before_quantity < quantity:
            raise HTTPException(status_code=400, detail="库存数量不足")
        after_quantity = before_quantity - quantity
    else:
        raise HTTPException(status_code=400, detail="库存操作类型不正确")

    item.quantity = after_quantity
    item.status = "used" if after_quantity <= 0 else "available"
    record = InventoryTransactionRecord(
        inventory_id=item.id,
        transaction_type=transaction_type,
        quantity=quantity,
        before_quantity=before_quantity,
        after_quantity=after_quantity,
        operator_name=operator_name,
        remark=remark,
    )
    db.add(record)
    return record


def reverse_inventory_transaction(
    transaction_id: int,
    operator_name: str | None,
    remark: str | None,
    db: Session,
) -> InventoryTransactionRecord:
    record = db.get(InventoryTransactionRecord, transaction_id)
    if not record:
        raise HTTPException(status_code=404, detail="流水记录不存在")
    if record.transaction_type not in ("in", "out"):
        raise HTTPException(status_code=400, detail="该流水类型不支持撤销")
    if record.reversed_transaction_id is not None:
        raise HTTPException(status_code=400, detail="该流水已撤销，不能重复撤销")
    existing_reversal = db.query(InventoryTransactionRecord).filter(InventoryTransactionRecord.reversed_transaction_id == record.id).first()
    if existing_reversal:
        raise HTTPException(status_code=400, detail="该流水已撤销，不能重复撤销")

    item = db.get(MaterialInventory, record.inventory_id)
    if not item:
        raise HTTPException(status_code=404, detail="库存不存在")

    reverse_type = "out" if record.transaction_type == "in" else "in"
    before_quantity = item.quantity
    if reverse_type == "out":
        if before_quantity < record.quantity:
            raise HTTPException(status_code=400, detail="当前库存不足，不能撤销该入库记录")
        after_quantity = before_quantity - record.quantity
    else:
        after_quantity = before_quantity + record.quantity

    item.quantity = after_quantity
    item.status = "used" if after_quantity <= 0 else "available"
    reversal = InventoryTransactionRecord(
        inventory_id=item.id,
        transaction_type=reverse_type,
        quantity=record.quantity,
        before_quantity=before_quantity,
        after_quantity=after_quantity,
        reversed_transaction_id=record.id,
        operator_name=operator_name,
        remark=remark or "撤销流水",
    )
    db.add(reversal)
    db.flush()
    record.reversed_transaction_id = reversal.id
    return reversal
