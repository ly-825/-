from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import InventoryTransactionRecord, MaterialInventory
from app.schemas import InventoryAdjust, InventoryCreate, InventoryOut, InventoryTransactionOut
from app.services.inventory_service import adjust_inventory_quantity

router = APIRouter()


@router.post("", response_model=InventoryOut)
def create_inventory(payload: InventoryCreate, db: Session = Depends(get_db)) -> MaterialInventory:
    item = MaterialInventory(**payload.model_dump())
    db.add(item)
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
        query = query.filter(MaterialInventory.inventory_type != "scrap")
    if status:
        query = query.filter(MaterialInventory.status == status)
    return query.order_by(MaterialInventory.created_at.desc()).all()


@router.get("/transactions/list", response_model=list[InventoryTransactionOut])
def list_inventory_transactions(db: Session = Depends(get_db)) -> list[InventoryTransactionRecord]:
    return db.query(InventoryTransactionRecord).order_by(InventoryTransactionRecord.created_at.desc()).limit(300).all()


@router.get("/{inventory_id}", response_model=InventoryOut)
def get_inventory(inventory_id: int, db: Session = Depends(get_db)) -> MaterialInventory:
    item = db.get(MaterialInventory, inventory_id)
    if not item:
        raise HTTPException(status_code=404, detail="库存不存在")
    return item


@router.post("/{inventory_id}/adjust", response_model=InventoryOut)
def adjust_inventory(inventory_id: int, payload: InventoryAdjust, db: Session = Depends(get_db)) -> MaterialInventory:
    item = db.get(MaterialInventory, inventory_id)
    if not item:
        raise HTTPException(status_code=404, detail="库存不存在")
    adjust_inventory_quantity(item, payload.transaction_type, payload.quantity, payload.operator_name, payload.remark, db)
    db.commit()
    db.refresh(item)
    return item

