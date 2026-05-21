from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import InventoryTransactionRecord, MaterialInventory


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
