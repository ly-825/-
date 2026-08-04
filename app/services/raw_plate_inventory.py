from __future__ import annotations

import math
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (
    InventoryTransactionRecord,
    MaterialInventory,
    RawPlateSpecification,
)
from app.services.inventory_service import reverse_inventory_transaction
from app.services.inventory_summaries import raw_plate_summary_rows
from app.services.material_formats import (
    normalize_steel_thickness,
    steel_dimension_sort_key,
    steel_spec_name,
)
from app.services.operation_log import inventory_snapshot, record_operation_log
from app.time_utils import china_now


def _optional_float(value: str | float | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _require_positive(value: float | None, label: str) -> float:
    if value is None or value <= 0:
        raise HTTPException(status_code=400, detail=f"{label}必须大于0")
    return float(value)


def list_raw_plate_specifications(
    db: Session,
    *,
    q: str = "",
    material: str = "",
    length: str = "",
    width: str = "",
    thickness: str = "",
) -> list[RawPlateSpecification]:
    query = db.query(RawPlateSpecification)
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            RawPlateSpecification.spec_name.ilike(like)
            | RawPlateSpecification.material.ilike(like)
            | RawPlateSpecification.remark.ilike(like)
        )
    if material.strip():
        query = query.filter(
            RawPlateSpecification.material.ilike(f"%{material.strip()}%")
        )
    for value, column in (
        (length, RawPlateSpecification.length),
        (width, RawPlateSpecification.width),
        (thickness, RawPlateSpecification.thickness),
    ):
        number = _optional_float(value)
        if number is not None:
            query = query.filter(column == number)
    specs = query.all()
    return sorted(
        specs,
        key=lambda spec: steel_dimension_sort_key(
            spec.thickness, spec.width, spec.length, spec.material
        ),
    )


def _duplicate_specification(
    db: Session,
    *,
    material: str,
    length: float,
    width: float,
    thickness: float,
    exclude_id: int | None = None,
) -> RawPlateSpecification | None:
    query = db.query(RawPlateSpecification).filter(
        RawPlateSpecification.material == material,
        RawPlateSpecification.length == length,
        RawPlateSpecification.width == width,
        RawPlateSpecification.thickness == thickness,
    )
    if exclude_id is not None:
        query = query.filter(RawPlateSpecification.id != exclude_id)
    return query.first()


def create_raw_plate_specification(
    db: Session,
    *,
    material: str,
    length: float,
    width: float,
    thickness: float,
    density: float,
    remark: str = "",
) -> RawPlateSpecification:
    material_value = material.strip()
    if not material_value:
        raise HTTPException(status_code=400, detail="材质不能为空")
    length_value = _require_positive(length, "长度")
    width_value = _require_positive(width, "宽度")
    thickness_value = normalize_steel_thickness(
        _require_positive(thickness, "厚度")
    )
    density_value = _require_positive(density, "密度")
    if _duplicate_specification(
        db,
        material=material_value,
        length=length_value,
        width=width_value,
        thickness=thickness_value,
    ):
        raise HTTPException(status_code=400, detail="相同材质和尺寸的板料规格已存在")
    spec = RawPlateSpecification(
        spec_name=steel_spec_name(thickness_value, width_value, length_value),
        material=material_value,
        length=length_value,
        width=width_value,
        thickness=thickness_value,
        density=density_value,
        remark=remark.strip() or None,
        is_active=1,
    )
    db.add(spec)
    db.flush()
    return spec


def update_raw_plate_specification(
    db: Session,
    spec_id: int,
    *,
    material: str,
    length: float,
    width: float,
    thickness: float,
    density: float,
    is_active: int,
    remark: str = "",
) -> RawPlateSpecification:
    spec = db.get(RawPlateSpecification, spec_id)
    if not spec:
        raise HTTPException(status_code=404, detail="板料规格不存在")
    material_value = material.strip()
    if not material_value:
        raise HTTPException(status_code=400, detail="材质不能为空")
    length_value = _require_positive(length, "长度")
    width_value = _require_positive(width, "宽度")
    thickness_value = normalize_steel_thickness(
        _require_positive(thickness, "厚度")
    )
    density_value = _require_positive(density, "密度")
    if _duplicate_specification(
        db,
        material=material_value,
        length=length_value,
        width=width_value,
        thickness=thickness_value,
        exclude_id=spec.id,
    ):
        raise HTTPException(status_code=400, detail="相同材质和尺寸的板料规格已存在")
    spec.spec_name = steel_spec_name(thickness_value, width_value, length_value)
    spec.material = material_value
    spec.length = length_value
    spec.width = width_value
    spec.thickness = thickness_value
    spec.density = density_value
    spec.is_active = 1 if is_active else 0
    spec.remark = remark.strip() or None
    db.flush()
    return spec


def toggle_raw_plate_specification(
    db: Session, spec_id: int
) -> RawPlateSpecification:
    spec = db.get(RawPlateSpecification, spec_id)
    if not spec:
        raise HTTPException(status_code=404, detail="板料规格不存在")
    spec.is_active = 0 if spec.is_active else 1
    db.flush()
    return spec


def list_raw_plate_batches(
    db: Session,
    *,
    q: str = "",
    material: str = "",
    length: str = "",
    width: str = "",
    thickness: str = "",
    location: str = "",
) -> list[MaterialInventory]:
    query = db.query(MaterialInventory).filter(
        MaterialInventory.inventory_type == "raw_plate"
    )
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            MaterialInventory.material_code.ilike(like)
            | MaterialInventory.raw_plate_model.ilike(like)
            | MaterialInventory.material.ilike(like)
            | MaterialInventory.location.ilike(like)
        )
    if material.strip():
        query = query.filter(MaterialInventory.material.ilike(f"%{material.strip()}%"))
    for value, column in (
        (length, MaterialInventory.length),
        (width, MaterialInventory.width),
        (thickness, MaterialInventory.thickness),
    ):
        number = _optional_float(value)
        if number is not None:
            query = query.filter(column == number)
    if location.strip():
        query = query.filter(MaterialInventory.location.ilike(f"%{location.strip()}%"))
    return query.order_by(
        MaterialInventory.created_at.asc(), MaterialInventory.id.asc()
    ).all()


def list_raw_plate_groups(db: Session, **filters) -> list[dict[str, Any]]:
    items = list_raw_plate_batches(db, **filters)
    groups = raw_plate_summary_rows(items, {})
    groups.sort(
        key=lambda group: steel_dimension_sort_key(
            group["thickness"], group["width"], group["length"], group["material"]
        )
    )
    return groups


def update_raw_plate_batch(
    db: Session,
    batch_id: int,
    *,
    raw_plate_model: str,
    material_code: str,
    material: str,
    length: float | None,
    width: float | None,
    thickness: float | None,
    location: str,
    status: str,
    operator_name: str = "",
    remark: str = "",
) -> MaterialInventory:
    item = db.get(MaterialInventory, batch_id)
    if not item or item.inventory_type != "raw_plate":
        raise HTTPException(status_code=404, detail="板料库存不存在")
    if item.quantity <= 0:
        raise HTTPException(status_code=400, detail="零库存板料不能修改")
    model_value = raw_plate_model.strip()
    if not model_value:
        raise HTTPException(status_code=400, detail="板料型号不能为空")
    if len(model_value) > 100:
        raise HTTPException(status_code=400, detail="板料型号不能超过100个字符")
    has_out_record = (
        db.query(InventoryTransactionRecord)
        .filter(
            InventoryTransactionRecord.inventory_id == item.id,
            InventoryTransactionRecord.transaction_type == "out",
        )
        .first()
        is not None
    )
    before_data = inventory_snapshot(item)
    item.raw_plate_model = model_value
    item.material_code = material_code.strip() or None
    item.location = location.strip() or None
    if not has_out_record:
        material_value = material.strip()
        if not material_value:
            raise HTTPException(status_code=400, detail="材质不能为空")
        length_value = _require_positive(length, "长度")
        width_value = _require_positive(width, "宽度")
        thickness_value = normalize_steel_thickness(
            _require_positive(thickness, "厚度")
        )
        if status not in ("available", "used"):
            raise HTTPException(status_code=400, detail="状态无效")
        item.material = material_value
        item.length = length_value
        item.width = width_value
        item.thickness = thickness_value
        item.usable_size = f"{steel_spec_name(thickness_value, width_value, length_value)}mm"
        item.status = status
    record_operation_log(
        db,
        "raw_plate_batch_update",
        "inventory",
        item.id,
        operator_name.strip() or None,
        remark.strip() or "修改板料批次",
        before_data=before_data,
        after_data=inventory_snapshot(item),
    )
    db.flush()
    return item


def inbound_raw_plate(
    db: Session,
    *,
    specification_id: int | None = None,
    raw_plate_model: str = "",
    material_code: str = "",
    material: str = "",
    total_weight_ton: float,
    length: float | None = None,
    width: float | None = None,
    thickness: float | None = None,
    density: float | None = 7.85,
    location: str = "",
    operator_name: str = "",
    remark: str = "",
) -> dict[str, Any]:
    if specification_id is not None:
        spec = db.get(RawPlateSpecification, specification_id)
        if not spec or not spec.is_active:
            raise HTTPException(status_code=400, detail="板料规格不存在或已停用")
        material = spec.material
        length = spec.length
        width = spec.width
        thickness = spec.thickness
        density = spec.density
        raw_plate_model = spec.spec_name
    material_value = material.strip()
    if not material_value:
        raise HTTPException(status_code=400, detail="材质不能为空")
    total_weight_value = _require_positive(total_weight_ton, "总重量")
    length_value = _require_positive(length, "长度")
    width_value = _require_positive(width, "宽度")
    thickness_value = normalize_steel_thickness(
        _require_positive(thickness, "厚度")
    )
    density_value = _require_positive(density, "密度")
    single_weight_kg = (
        length_value * width_value * thickness_value * density_value / 1_000_000
    )
    total_weight_kg = total_weight_value * 1000
    quantity = math.floor(total_weight_kg / single_weight_kg)
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="总重量不足一块板料")
    remaining_weight_kg = total_weight_kg - quantity * single_weight_kg
    model_value = steel_spec_name(thickness_value, width_value, length_value)
    batch_code = material_code.strip() or f"RAW-{china_now().strftime('%Y%m%d%H%M%S%f')}"
    item = MaterialInventory(
        material_code=batch_code,
        raw_plate_model=model_value,
        inventory_type="raw_plate",
        material=material_value,
        thickness=thickness_value,
        shape="rectangle",
        length=length_value,
        width=width_value,
        usable_size=f"{model_value}mm",
        quantity=quantity,
        location=location.strip() or None,
        status="available",
    )
    db.add(item)
    db.flush()
    transaction_remark = (
        f"{remark.strip() or '板料入库'}；总重量{total_weight_value:g}吨，"
        f"密度{density_value:g}g/cm³，单块约{single_weight_kg:.3f}kg，"
        f"入库{quantity}块，余重约{remaining_weight_kg:.3f}kg"
    )
    transaction = InventoryTransactionRecord(
        inventory_id=item.id,
        transaction_type="in",
        quantity=quantity,
        before_quantity=0,
        after_quantity=quantity,
        operator_name=operator_name.strip() or None,
        remark=transaction_remark,
    )
    db.add(transaction)
    record_operation_log(
        db,
        "raw_plate_inbound",
        "inventory",
        item.id,
        operator_name.strip() or None,
        transaction_remark,
        after_data=inventory_snapshot(item),
    )
    db.flush()
    return {
        "item": item,
        "transaction": transaction,
        "single_weight_kg": single_weight_kg,
        "total_weight_kg": total_weight_kg,
        "quantity": quantity,
        "remaining_weight_kg": remaining_weight_kg,
    }


def outbound_raw_plate_fifo(
    db: Session,
    *,
    material: str,
    length: float,
    width: float,
    thickness: float,
    quantity: int,
    location: str = "",
    customer_name: str = "",
    operator_name: str = "",
    remark: str = "",
) -> dict[str, Any]:
    material_value = material.strip()
    if not material_value:
        raise HTTPException(status_code=400, detail="材质不能为空")
    length_value = _require_positive(length, "长度")
    width_value = _require_positive(width, "宽度")
    thickness_value = normalize_steel_thickness(
        _require_positive(thickness, "厚度")
    )
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="出库块数必须大于0")
    query = db.query(MaterialInventory).filter(
        MaterialInventory.inventory_type == "raw_plate",
        MaterialInventory.material == material_value,
        MaterialInventory.length == length_value,
        MaterialInventory.width == width_value,
        MaterialInventory.thickness == thickness_value,
        MaterialInventory.quantity > 0,
    )
    location_value = location.strip()
    if location_value:
        query = query.filter(MaterialInventory.location == location_value)
    batches = query.order_by(
        MaterialInventory.created_at.asc(), MaterialInventory.id.asc()
    ).all()
    available_quantity = sum(item.quantity for item in batches)
    if available_quantity < quantity:
        raise HTTPException(
            status_code=400,
            detail=f"板料库存不足，当前可出库 {available_quantity} 块",
        )
    remaining = quantity
    allocations: list[dict[str, Any]] = []
    customer_value = customer_name.strip() or None
    for item in batches:
        if remaining <= 0:
            break
        deduction = min(item.quantity, remaining)
        before_data = inventory_snapshot(item)
        before_quantity = item.quantity
        item.quantity -= deduction
        item.status = "used" if item.quantity <= 0 else "available"
        transaction = InventoryTransactionRecord(
            inventory_id=item.id,
            transaction_type="out",
            quantity=deduction,
            before_quantity=before_quantity,
            after_quantity=item.quantity,
            operator_name=operator_name.strip() or None,
            customer_name=customer_value,
            remark=remark.strip() or "板料出库",
        )
        db.add(transaction)
        db.flush()
        allocations.append(
            {
                "inventory_id": item.id,
                "transaction_id": transaction.id,
                "batch_code": item.material_code,
                "quantity": deduction,
                "before_quantity": before_quantity,
                "after_quantity": item.quantity,
            }
        )
        record_operation_log(
            db,
            "raw_plate_outbound",
            "inventory",
            item.id,
            operator_name.strip() or None,
            transaction.remark,
            before_data=before_data,
            after_data=inventory_snapshot(item),
        )
        remaining -= deduction
    db.flush()
    return {
        "message": "板料出库成功",
        "before_quantity": available_quantity,
        "after_quantity": available_quantity - quantity,
        "quantity": quantity,
        "allocations": allocations,
    }


def list_raw_plate_transactions(
    db: Session,
    *,
    q: str = "",
    material: str = "",
    transaction_type: str = "",
) -> list[dict[str, Any]]:
    query = db.query(InventoryTransactionRecord)
    if transaction_type.strip():
        query = query.filter(
            InventoryTransactionRecord.transaction_type == transaction_type.strip()
        )
    rows: list[dict[str, Any]] = []
    keyword = q.strip().lower()
    for record in query.order_by(InventoryTransactionRecord.created_at.desc()).limit(500):
        item = db.get(MaterialInventory, record.inventory_id)
        if not item or item.inventory_type != "raw_plate":
            continue
        if material.strip() and material.strip().lower() not in item.material.lower():
            continue
        searchable = " ".join(
            str(value or "")
            for value in (
                item.material_code,
                item.raw_plate_model,
                item.material,
                item.location,
                record.customer_name,
                record.operator_name,
                record.remark,
            )
        ).lower()
        if keyword and keyword not in searchable:
            continue
        rows.append(
            {
                "id": record.id,
                "inventory_id": item.id,
                "transaction_type": record.transaction_type,
                "quantity": record.quantity,
                "before_quantity": record.before_quantity,
                "after_quantity": record.after_quantity,
                "reversed_transaction_id": record.reversed_transaction_id,
                "operator_name": record.operator_name,
                "customer_name": record.customer_name,
                "remark": record.remark,
                "created_at": record.created_at,
                "batch": item,
            }
        )
    return rows


def reverse_raw_plate_transaction(
    db: Session,
    transaction_id: int,
    *,
    operator_name: str = "",
    remark: str = "",
) -> InventoryTransactionRecord:
    record = db.get(InventoryTransactionRecord, transaction_id)
    if not record:
        raise HTTPException(status_code=404, detail="板料流水不存在")
    item = db.get(MaterialInventory, record.inventory_id)
    if not item or item.inventory_type != "raw_plate":
        raise HTTPException(status_code=400, detail="该流水不是板料流水")
    reversal = reverse_inventory_transaction(
        transaction_id,
        operator_name.strip() or None,
        remark.strip() or "撤回板料流水",
        db,
    )
    db.flush()
    record_operation_log(
        db,
        "raw_plate_transaction_reverse",
        "inventory_transaction",
        transaction_id,
        operator_name.strip() or None,
        remark.strip() or "撤回板料流水",
        after_data={"reversal_transaction_id": reversal.id},
    )
    return reversal
