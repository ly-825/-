from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.assistant.render import table
from app.assistant.types import AssistantAction, AssistantIntent, AssistantResponse
from app.models import InventoryTransactionRecord, MaterialInventory


def query_transactions(intent: AssistantIntent, db: Session) -> AssistantResponse:
    transaction_type = _transaction_type(intent)
    entity = intent.get("entity")
    start, end, label = _date_range(intent)
    records = (
        db.query(InventoryTransactionRecord)
        .filter(
            InventoryTransactionRecord.transaction_type == transaction_type,
            InventoryTransactionRecord.reversed_transaction_id.is_(None),
            InventoryTransactionRecord.created_at >= start,
            InventoryTransactionRecord.created_at < end,
        )
        .order_by(InventoryTransactionRecord.created_at.desc())
        .limit(300)
        .all()
    )
    inventory_ids = [record.inventory_id for record in records]
    inventory_map = {
        item.id: item
        for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()
    } if inventory_ids else {}
    rows = []
    for record in records:
        item = inventory_map.get(record.inventory_id)
        if not item:
            continue
        if entity in ("product", "raw_plate", "scrap") and item.inventory_type != entity:
            continue
        rows.append(
            {
                "time": record.created_at.strftime("%Y-%m-%d %H:%M:%S") if record.created_at else "",
                "type": _inventory_type_label(item.inventory_type),
                "code": item.material_code or item.source_product_code or item.material or "-",
                "material": item.material,
                "size": item.usable_size or _size_label(item),
                "quantity": record.quantity,
                "before_quantity": record.before_quantity,
                "after_quantity": record.after_quantity,
                "location": item.location or "-",
                "operator": record.operator_name or "-",
                "remark": record.remark or "-",
            }
        )
    action_label = "入库" if transaction_type == "in" else "出库"
    return AssistantResponse(
        answer=f"{label}{_entity_label(entity)}{action_label}明细共 {len(rows)} 条。",
        data=table(
            f"{label}{_entity_label(entity)}{action_label}明细",
            [
                {"prop": "time", "label": "时间"},
                {"prop": "type", "label": "类型"},
                {"prop": "code", "label": "编号/规格"},
                {"prop": "material", "label": "材质"},
                {"prop": "size", "label": "尺寸"},
                {"prop": "quantity", "label": "数量"},
                {"prop": "before_quantity", "label": "操作前"},
                {"prop": "after_quantity", "label": "操作后"},
                {"prop": "location", "label": "库位"},
                {"prop": "operator", "label": "操作人"},
                {"prop": "remark", "label": "备注"},
            ],
            rows,
        ),
        actions=_actions(entity),
    )


def _transaction_type(intent: AssistantIntent) -> str:
    action = intent.get("action")
    name = intent.get("intent")
    if action == "inbound" or name in ("inbound_detail", "inbound_summary", "inbound_ranking"):
        return "in"
    return "out"


def _date_range(intent: AssistantIntent) -> tuple[datetime, datetime, str]:
    time_range = intent.get("time_range") or {}
    start_date = time_range.get("start_date")
    end_date = time_range.get("end_date")
    if start_date and end_date:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        return start, end, f"{start_date} 至 {end_date}"
    now = datetime.now()
    range_type = time_range.get("type")
    if range_type == "this_year":
        return datetime(now.year, 1, 1), now + timedelta(days=1), "本年"
    if range_type == "this_month":
        return datetime(now.year, now.month, 1), now + timedelta(days=1), "本月"
    if range_type == "this_week":
        start = datetime(now.year, now.month, now.day) - timedelta(days=now.weekday())
        return start, now + timedelta(days=1), "本周"
    start = datetime(now.year, now.month, now.day)
    return start, start + timedelta(days=1), "今天"


def _inventory_type_label(value: str) -> str:
    return {"product": "产品", "raw_plate": "板料", "scrap": "余料"}.get(value, value)


def _entity_label(value: str | None) -> str:
    return {"product": "产品", "raw_plate": "板料", "scrap": "余料"}.get(value or "", "")


def _size_label(item: MaterialInventory) -> str:
    if item.length or item.width or item.thickness:
        return f"{item.length or '-'}×{item.width or '-'}×{item.thickness or '-'}"
    return "-"


def _actions(entity: str | None) -> list[AssistantAction]:
    if entity == "raw_plate":
        return [AssistantAction("板料流水", "/admin/raw-plates/transactions"), AssistantAction("板料库存", "/admin/raw-plates")]
    if entity == "scrap":
        return [AssistantAction("余料流水", "/admin/scraps/transactions"), AssistantAction("余料记录", "/admin/scraps")]
    return [AssistantAction("库存流水", "/admin/inventory/transactions"), AssistantAction("产品库存", "/admin/inventory")]

