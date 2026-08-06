from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import PaperInventoryBatch, PaperInventoryTransaction, PaperSpecification
from app.services.drawing_search import natural_sort_key
from app.services.material_formats import paper_roll_size, paper_sheet_model
from app.services.operation_log import record_operation_log
from app.time_utils import china_now


PAPER_TYPES = {"roll", "sheet"}
PAPER_TYPE_LABELS = {"roll": "纸圈", "sheet": "纸张"}
PAPER_UNITS = {"roll": "圈", "sheet": "张"}


def _require_positive(value: float | None, label: str) -> float:
    if value is None or value <= 0:
        raise HTTPException(status_code=400, detail=f"{label}必须大于0")
    return float(value)


def normalize_paper_specification(
    paper_type: str,
    model: str,
    material_name: str,
    thickness: float,
    inner_diameter: float | None,
    outer_diameter: float | None,
    length: float | None,
    width: float | None,
) -> dict[str, object]:
    type_value = paper_type.strip().lower()
    if type_value not in PAPER_TYPES:
        raise HTTPException(status_code=400, detail="纸材类型无效")
    material_value = material_name.strip()
    if not material_value:
        raise HTTPException(status_code=400, detail="纸材名称/材质不能为空")
    thickness_value = _require_positive(thickness, "厚度")
    if type_value == "roll":
        model_value = model.strip()
        if not model_value:
            raise HTTPException(status_code=400, detail="纸圈型号不能为空")
        inner_value = _require_positive(inner_diameter, "内径")
        outer_value = _require_positive(outer_diameter, "外径")
        if outer_value <= inner_value:
            raise HTTPException(status_code=400, detail="纸圈外径必须大于内径")
        return {
            "paper_type": type_value,
            "model": model_value,
            "material_name": material_value,
            "thickness": thickness_value,
            "inner_diameter": inner_value,
            "outer_diameter": outer_value,
            "length": None,
            "width": None,
        }
    length_value = _require_positive(length, "长度")
    width_value = _require_positive(width, "宽度")
    return {
        "paper_type": type_value,
        "model": paper_sheet_model(thickness_value, length_value, width_value),
        "material_name": material_value,
        "thickness": thickness_value,
        "inner_diameter": None,
        "outer_diameter": None,
        "length": length_value,
        "width": width_value,
    }


def paper_specification_sort_key(spec: PaperSpecification) -> tuple:
    if spec.paper_type == "roll":
        dimensions = (spec.inner_diameter or 0, spec.outer_diameter or 0)
    else:
        dimensions = (spec.length or 0, spec.width or 0)
    return (
        0 if spec.paper_type == "roll" else 1,
        natural_sort_key(spec.model),
        natural_sort_key(spec.material_name),
        spec.thickness,
        *dimensions,
    )


def paper_batch_size(batch: PaperInventoryBatch) -> str:
    if batch.paper_type == "roll":
        return paper_roll_size(batch.thickness, batch.inner_diameter, batch.outer_diameter)
    return paper_sheet_model(batch.thickness, batch.length, batch.width)


def paper_inventory_group_key(batch: PaperInventoryBatch) -> tuple:
    return (
        batch.specification_id,
        batch.paper_type,
        batch.model,
        batch.material_name,
        batch.thickness,
        batch.inner_diameter,
        batch.outer_diameter,
        batch.length,
        batch.width,
    )


def paper_inventory_groups(batches: list[PaperInventoryBatch]) -> list[dict[str, Any]]:
    grouped: dict[tuple, dict[str, Any]] = {}
    for batch in batches:
        if batch.quantity <= 0:
            continue
        key = paper_inventory_group_key(batch)
        group = grouped.setdefault(
            key,
            {
                "specification_id": batch.specification_id,
                "paper_type": batch.paper_type,
                "type_label": PAPER_TYPE_LABELS[batch.paper_type],
                "model": batch.model,
                "material_name": batch.material_name,
                "thickness": batch.thickness,
                "inner_diameter": batch.inner_diameter,
                "outer_diameter": batch.outer_diameter,
                "length": batch.length,
                "width": batch.width,
                "size": paper_batch_size(batch),
                "unit": PAPER_UNITS[batch.paper_type],
                "quantity": 0,
                "batch_count": 0,
                "locations": set(),
                "batch_ids": set(),
                "price_min": batch.unit_price,
                "price_max": batch.unit_price,
                "latest": batch.updated_at or batch.created_at,
            },
        )
        group["quantity"] += batch.quantity
        group["batch_count"] += 1
        group["batch_ids"].add(batch.id)
        if batch.location:
            group["locations"].add(batch.location)
        group["price_min"] = min(group["price_min"], batch.unit_price)
        group["price_max"] = max(group["price_max"], batch.unit_price)
        batch_time = batch.updated_at or batch.created_at
        if batch_time and (not group["latest"] or batch_time > group["latest"]):
            group["latest"] = batch_time
    return sorted(
        grouped.values(),
        key=lambda group: (
            0 if group["paper_type"] == "roll" else 1,
            natural_sort_key(group["model"]),
            natural_sort_key(group["material_name"]),
            group["thickness"],
            group["inner_diameter"] or group["length"] or 0,
            group["outer_diameter"] or group["width"] or 0,
        ),
    )


def list_paper_specifications(
    db: Session,
    *,
    q: str = "",
    paper_type: str = "",
    material_name: str = "",
) -> list[PaperSpecification]:
    query = db.query(PaperSpecification)
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            PaperSpecification.model.ilike(like)
            | PaperSpecification.material_name.ilike(like)
            | PaperSpecification.remark.ilike(like)
        )
    type_value = paper_type.strip().lower()
    if type_value:
        if type_value not in PAPER_TYPES:
            raise HTTPException(status_code=400, detail="纸材类型无效")
        query = query.filter(PaperSpecification.paper_type == type_value)
    if material_name.strip():
        query = query.filter(
            PaperSpecification.material_name.ilike(f"%{material_name.strip()}%")
        )
    specs = query.all()
    specs.sort(key=paper_specification_sort_key)
    return specs


def _apply_specification_values(
    specification: PaperSpecification, values: dict[str, object]
) -> None:
    for field, value in values.items():
        setattr(specification, field, value)


def create_paper_specification(
    db: Session,
    *,
    paper_type: str,
    model: str,
    material_name: str,
    thickness: float,
    inner_diameter: float | None,
    outer_diameter: float | None,
    length: float | None,
    width: float | None,
    remark: str = "",
) -> PaperSpecification:
    values = normalize_paper_specification(
        paper_type,
        model,
        material_name,
        thickness,
        inner_diameter,
        outer_diameter,
        length,
        width,
    )
    duplicate = (
        db.query(PaperSpecification)
        .filter(
            PaperSpecification.paper_type == values["paper_type"],
            PaperSpecification.model == values["model"],
            PaperSpecification.material_name == values["material_name"],
            PaperSpecification.thickness == values["thickness"],
        )
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=400, detail="相同纸材规格已存在")
    specification = PaperSpecification(
        **values,
        remark=remark.strip() or None,
        is_active=1,
    )
    db.add(specification)
    db.flush()
    return specification


def update_paper_specification(
    db: Session,
    specification_id: int,
    *,
    paper_type: str,
    model: str,
    material_name: str,
    thickness: float,
    inner_diameter: float | None,
    outer_diameter: float | None,
    length: float | None,
    width: float | None,
    is_active: int,
    remark: str = "",
) -> PaperSpecification:
    specification = db.get(PaperSpecification, specification_id)
    if not specification:
        raise HTTPException(status_code=404, detail="纸材规格不存在")
    values = normalize_paper_specification(
        paper_type,
        model,
        material_name,
        thickness,
        inner_diameter,
        outer_diameter,
        length,
        width,
    )
    duplicate = (
        db.query(PaperSpecification)
        .filter(
            PaperSpecification.id != specification.id,
            PaperSpecification.paper_type == values["paper_type"],
            PaperSpecification.model == values["model"],
            PaperSpecification.material_name == values["material_name"],
            PaperSpecification.thickness == values["thickness"],
        )
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=400, detail="相同纸材规格已存在")
    _apply_specification_values(specification, values)
    specification.is_active = 1 if is_active else 0
    specification.remark = remark.strip() or None
    db.flush()
    return specification


def toggle_paper_specification(
    db: Session, specification_id: int
) -> PaperSpecification:
    specification = db.get(PaperSpecification, specification_id)
    if not specification:
        raise HTTPException(status_code=404, detail="纸材规格不存在")
    specification.is_active = 0 if specification.is_active else 1
    db.flush()
    return specification


def list_paper_batches(
    db: Session,
    specification_id: int,
    *,
    q: str = "",
    location: str = "",
    include_zero: bool = False,
) -> list[PaperInventoryBatch]:
    if not db.get(PaperSpecification, specification_id):
        raise HTTPException(status_code=404, detail="纸材规格不存在")
    query = db.query(PaperInventoryBatch).filter(
        PaperInventoryBatch.specification_id == specification_id
    )
    if not include_zero:
        query = query.filter(PaperInventoryBatch.quantity > 0)
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            PaperInventoryBatch.batch_code.ilike(like)
            | PaperInventoryBatch.model.ilike(like)
            | PaperInventoryBatch.material_name.ilike(like)
        )
    if location.strip():
        query = query.filter(PaperInventoryBatch.location.ilike(f"%{location.strip()}%"))
    return query.order_by(
        PaperInventoryBatch.created_at.asc(), PaperInventoryBatch.id.asc()
    ).all()


def list_paper_inventory(
    db: Session,
    *,
    q: str = "",
    paper_type: str = "",
    material_name: str = "",
    location: str = "",
) -> list[dict[str, Any]]:
    query = db.query(PaperInventoryBatch).filter(PaperInventoryBatch.quantity > 0)
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            PaperInventoryBatch.batch_code.ilike(like)
            | PaperInventoryBatch.model.ilike(like)
            | PaperInventoryBatch.material_name.ilike(like)
        )
    type_value = paper_type.strip().lower()
    if type_value:
        if type_value not in PAPER_TYPES:
            raise HTTPException(status_code=400, detail="纸材类型无效")
        query = query.filter(PaperInventoryBatch.paper_type == type_value)
    if material_name.strip():
        query = query.filter(
            PaperInventoryBatch.material_name.ilike(f"%{material_name.strip()}%")
        )
    if location.strip():
        query = query.filter(PaperInventoryBatch.location.ilike(f"%{location.strip()}%"))
    return paper_inventory_groups(query.all())


def inbound_paper(
    db: Session,
    *,
    specification_id: int,
    batch_code: str,
    quantity: int,
    unit_price: str | float | Decimal,
    location: str = "",
    operator_name: str = "",
    remark: str = "",
) -> dict[str, object]:
    specification = db.get(PaperSpecification, specification_id)
    if not specification or not specification.is_active:
        raise HTTPException(status_code=400, detail="纸材规格不存在或已停用")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="入库数量必须大于0")
    try:
        price = Decimal(str(unit_price)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except InvalidOperation as exc:
        raise HTTPException(status_code=400, detail="入库单价格式错误") from exc
    if price < 0:
        raise HTTPException(status_code=400, detail="入库单价不能小于0")
    batch = PaperInventoryBatch(
        specification_id=specification.id,
        batch_code=batch_code.strip()
        or f"PAPER-{china_now().strftime('%Y%m%d%H%M%S%f')}",
        paper_type=specification.paper_type,
        model=specification.model,
        material_name=specification.material_name,
        thickness=specification.thickness,
        inner_diameter=specification.inner_diameter,
        outer_diameter=specification.outer_diameter,
        length=specification.length,
        width=specification.width,
        quantity=quantity,
        unit_price=price,
        location=location.strip() or None,
        status="available",
    )
    db.add(batch)
    db.flush()
    transaction = PaperInventoryTransaction(
        inventory_id=batch.id,
        transaction_type="in",
        quantity=quantity,
        before_quantity=0,
        after_quantity=quantity,
        operator_name=operator_name.strip() or None,
        remark=remark.strip() or "纸材入库",
    )
    db.add(transaction)
    record_operation_log(
        db,
        "paper_inbound",
        "paper_inventory",
        batch.id,
        operator_name.strip() or None,
        transaction.remark,
        after_data=paper_inventory_snapshot(batch),
    )
    db.flush()
    return {"batch": batch, "transaction": transaction}


def list_paper_transactions(
    db: Session,
    *,
    q: str = "",
    paper_type: str = "",
    transaction_type: str = "",
) -> list[dict[str, Any]]:
    query = db.query(PaperInventoryTransaction)
    if transaction_type.strip():
        query = query.filter(
            PaperInventoryTransaction.transaction_type == transaction_type.strip()
        )
    rows: list[dict[str, Any]] = []
    keyword = q.strip().lower()
    for record in query.order_by(PaperInventoryTransaction.created_at.desc()).limit(500):
        batch = db.get(PaperInventoryBatch, record.inventory_id)
        if not batch:
            continue
        if paper_type.strip() and batch.paper_type != paper_type.strip():
            continue
        searchable = " ".join(
            str(value or "")
            for value in (
                batch.batch_code,
                batch.model,
                batch.material_name,
                batch.location,
                record.customer_name,
                record.operator_name,
                record.remark,
            )
        ).lower()
        if keyword and keyword not in searchable:
            continue
        rows.append({"record": record, "batch": batch})
    return rows


def outbound_paper_fifo(
    specification_id: int,
    quantity: int,
    location: str | None,
    customer_name: str | None,
    operator_name: str | None,
    remark: str | None,
    db: Session,
) -> list[PaperInventoryTransaction]:
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="出库数量必须大于0")
    spec = db.get(PaperSpecification, specification_id)
    if not spec:
        raise HTTPException(status_code=404, detail="纸材规格不存在")
    query = db.query(PaperInventoryBatch).filter(
        PaperInventoryBatch.specification_id == specification_id,
        PaperInventoryBatch.quantity > 0,
    )
    location_value = (location or "").strip()
    if location_value:
        query = query.filter(PaperInventoryBatch.location == location_value)
    batches = query.order_by(PaperInventoryBatch.created_at.asc(), PaperInventoryBatch.id.asc()).all()
    available = sum(batch.quantity for batch in batches)
    if available < quantity:
        raise HTTPException(
            status_code=400,
            detail=f"纸材库存不足，当前可出库 {available} {PAPER_UNITS[spec.paper_type]}",
        )
    remaining = quantity
    records: list[PaperInventoryTransaction] = []
    for batch in batches:
        if remaining <= 0:
            break
        outbound_quantity = min(batch.quantity, remaining)
        before_data = paper_inventory_snapshot(batch)
        before_quantity = batch.quantity
        batch.quantity -= outbound_quantity
        batch.status = "used" if batch.quantity <= 0 else "available"
        record = PaperInventoryTransaction(
            inventory_id=batch.id,
            transaction_type="out",
            quantity=outbound_quantity,
            before_quantity=before_quantity,
            after_quantity=batch.quantity,
            operator_name=(operator_name or "").strip() or None,
            customer_name=(customer_name or "").strip() or None,
            remark=(remark or "").strip() or "纸材出库",
        )
        db.add(record)
        records.append(record)
        record_operation_log(
            db,
            "paper_outbound",
            "paper_inventory",
            batch.id,
            (operator_name or "").strip() or None,
            record.remark,
            before_data=before_data,
            after_data=paper_inventory_snapshot(batch),
        )
        remaining -= outbound_quantity
    db.flush()
    return records


def reverse_paper_transaction(
    transaction_id: int,
    operator_name: str | None,
    remark: str | None,
    db: Session,
) -> PaperInventoryTransaction:
    record = db.get(PaperInventoryTransaction, transaction_id)
    if not record:
        raise HTTPException(status_code=404, detail="纸材流水不存在")
    if record.transaction_type not in ("in", "out"):
        raise HTTPException(status_code=400, detail="该纸材流水类型不支持撤回")
    existing = db.query(PaperInventoryTransaction).filter(
        PaperInventoryTransaction.reversed_transaction_id == record.id
    ).first()
    if record.reversed_transaction_id is not None or existing:
        raise HTTPException(status_code=400, detail="该纸材流水已撤回，不能重复撤回")
    batch = db.get(PaperInventoryBatch, record.inventory_id)
    if not batch:
        raise HTTPException(status_code=404, detail="纸材库存批次不存在")
    reverse_type = "out" if record.transaction_type == "in" else "in"
    before_quantity = batch.quantity
    if reverse_type == "out":
        if before_quantity < record.quantity:
            raise HTTPException(status_code=400, detail="当前纸材库存不足，不能撤回该入库流水")
        after_quantity = before_quantity - record.quantity
    else:
        after_quantity = before_quantity + record.quantity
    batch.quantity = after_quantity
    batch.status = "used" if after_quantity <= 0 else "available"
    reversal = PaperInventoryTransaction(
        inventory_id=batch.id,
        transaction_type=reverse_type,
        quantity=record.quantity,
        before_quantity=before_quantity,
        after_quantity=after_quantity,
        reversed_transaction_id=record.id,
        operator_name=(operator_name or "").strip() or None,
        customer_name=record.customer_name,
        remark=(remark or "").strip() or "撤回纸材流水",
    )
    db.add(reversal)
    db.flush()
    record.reversed_transaction_id = reversal.id
    record_operation_log(
        db,
        "paper_transaction_reverse",
        "paper_inventory",
        batch.id,
        (operator_name or "").strip() or None,
        reversal.remark,
        after_data=paper_inventory_snapshot(batch),
    )
    return reversal


def paper_inventory_snapshot(batch: PaperInventoryBatch | None) -> dict[str, Any] | None:
    if not batch:
        return None
    return {
        "id": batch.id,
        "specification_id": batch.specification_id,
        "batch_code": batch.batch_code,
        "paper_type": batch.paper_type,
        "model": batch.model,
        "material_name": batch.material_name,
        "size": paper_batch_size(batch),
        "quantity": batch.quantity,
        "unit_price": f"{batch.unit_price:.2f}",
        "location": batch.location,
        "status": batch.status,
    }
