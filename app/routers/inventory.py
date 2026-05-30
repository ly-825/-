from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import InventoryTransactionRecord, MaterialInventory
from app.schemas import InventoryAdjust, InventoryCreate, InventoryOut, InventoryTransactionOut, TransactionReverse
from app.services.inventory_service import adjust_inventory_quantity, reject_direct_inventory_write, reverse_inventory_transaction
from app.services.operation_log import inventory_snapshot, record_operation_log

router = APIRouter()


@router.post("", response_model=InventoryOut)
def create_inventory(payload: InventoryCreate, db: Session = Depends(get_db)) -> MaterialInventory:
    reject_direct_inventory_write()
    item = MaterialInventory(**payload.model_dump())
    db.add(item)
    db.flush()
    record_operation_log(db, "inventory_create", "inventory", item.id, None, "API创建库存", after_data=inventory_snapshot(item))
    db.commit()
    db.refresh(item)
    return item


@router.get("", response_model=list[InventoryOut])
def list_inventory(
    status: str | None = None,
    inventory_type: str | None = None,
    db: Session = Depends(get_db),
) -> list[MaterialInventory]:
    query = db.query(MaterialInventory)
    if inventory_type:
        query = query.filter(MaterialInventory.inventory_type == inventory_type)
    else:
        query = query.filter(MaterialInventory.inventory_type == "product")
    if status:
        query = query.filter(MaterialInventory.status == status)
    return query.order_by(MaterialInventory.created_at.desc()).all()


@router.get("/transactions/list", response_model=list[InventoryTransactionOut])
def list_inventory_transactions(db: Session = Depends(get_db)) -> list[InventoryTransactionRecord]:
    return db.query(InventoryTransactionRecord).order_by(InventoryTransactionRecord.created_at.desc()).limit(300).all()


@router.post("/transactions/{transaction_id}/reverse", response_model=InventoryTransactionOut)
def reverse_transaction(transaction_id: int, payload: TransactionReverse, db: Session = Depends(get_db)) -> InventoryTransactionRecord:
    reversal = reverse_inventory_transaction(transaction_id, payload.operator_name, payload.remark, db)
    db.flush()
    record_operation_log(
        db,
        "transaction_reverse",
        "inventory_transaction",
        transaction_id,
        payload.operator_name or None,
        payload.remark or "API撤销库存流水",
        after_data={"reversal_transaction_id": reversal.id},
    )
    db.commit()
    db.refresh(reversal)
    return reversal


@router.get("/{inventory_id}", response_model=InventoryOut)
def get_inventory(inventory_id: int, db: Session = Depends(get_db)) -> MaterialInventory:
    item = db.get(MaterialInventory, inventory_id)
    if not item:
        raise HTTPException(status_code=404, detail="库存不存在")
    return item


@router.post("/{inventory_id}/adjust", response_model=InventoryOut)
def adjust_inventory(inventory_id: int, payload: InventoryAdjust, db: Session = Depends(get_db)) -> MaterialInventory:
    reject_direct_inventory_write()
    item = db.get(MaterialInventory, inventory_id)
    if not item:
        raise HTTPException(status_code=404, detail="库存不存在")
    before_data = inventory_snapshot(item)
    record = adjust_inventory_quantity(item, payload.transaction_type, payload.quantity, payload.operator_name, payload.remark, db)
    db.flush()
    record_operation_log(
        db,
        "inventory_adjust",
        "inventory",
        item.id,
        payload.operator_name or None,
        payload.remark or record.remark,
        before_data=before_data,
        after_data=inventory_snapshot(item),
    )
    db.commit()
    db.refresh(item)
    return item
