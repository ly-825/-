from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.assistant.render import table
from app.assistant.types import AssistantAction, AssistantIntent, AssistantResponse
from app.models import MaterialInventory, ProductDrawing
from app.time_utils import china_now


def warning_list(intent: AssistantIntent, db: Session) -> AssistantResponse:
    rows = []
    low_products = _low_product_rows(db)
    pending_scraps = _pending_scrap_rows(db)
    idle_items = _idle_inventory_rows(db)
    confirmed_without_inventory = _confirmed_without_inventory_rows(db)
    rows.extend(low_products)
    rows.extend(pending_scraps)
    rows.extend(idle_items)
    rows.extend(confirmed_without_inventory)
    severity_order = {"高": 0, "中": 1, "低": 2}
    rows.sort(key=lambda row: (severity_order.get(row["level"], 9), row["type"], row["object"]))
    return AssistantResponse(
        answer=f"当前发现 {len(rows)} 条提醒，其中高优先级 {sum(1 for row in rows if row['level'] == '高')} 条。",
        data=table(
            "异常提醒",
            [
                {"prop": "level", "label": "等级"},
                {"prop": "type", "label": "类型"},
                {"prop": "object", "label": "对象"},
                {"prop": "quantity", "label": "数量"},
                {"prop": "reason", "label": "原因"},
                {"prop": "action", "label": "建议"},
            ],
            rows[:100],
            "warning_table",
        ),
        actions=[
            AssistantAction("待入库余料", "/admin/scraps/pending"),
            AssistantAction("库存查询", "/admin/inventory"),
            AssistantAction("操作日志", "/admin/operation-logs"),
        ],
    )


def _low_product_rows(db: Session) -> list[dict]:
    items = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "product").all()
    grouped: dict[str, int] = {}
    for item in items:
        code = item.material_code or item.source_product_code or "未编号"
        grouped[code] = grouped.get(code, 0) + item.quantity
    return [
        {"level": "高", "type": "产品低库存", "object": code, "quantity": quantity, "reason": "库存低于 10 件", "action": "评估是否需要补充入库"}
        for code, quantity in grouped.items()
        if quantity < 10
    ]


def _pending_scrap_rows(db: Session) -> list[dict]:
    deadline = china_now() - timedelta(days=3)
    items = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.status == "pending").all()
    rows = []
    for item in items:
        level = "高" if item.created_at and item.created_at <= deadline else "中"
        rows.append(
            {
                "level": level,
                "type": "余料待确认",
                "object": item.source_product_code or item.material,
                "quantity": item.quantity,
                "reason": "产品入库后生成的余料尚未确认实际尺寸和库位",
                "action": "到待入库余料页面确认",
            }
        )
    return rows


def _idle_inventory_rows(db: Session) -> list[dict]:
    deadline = china_now() - timedelta(days=90)
    items = (
        db.query(MaterialInventory)
        .filter(MaterialInventory.quantity > 0, MaterialInventory.updated_at <= deadline)
        .order_by(MaterialInventory.updated_at.asc())
        .limit(50)
        .all()
    )
    return [
        {
            "level": "低",
            "type": "长期未变化",
            "object": item.material_code or item.source_product_code or item.material,
            "quantity": item.quantity,
            "reason": "超过 90 天库存未变化",
            "action": "复核是否积压或库位信息是否准确",
        }
        for item in items
    ]


def _confirmed_without_inventory_rows(db: Session) -> list[dict]:
    drawings = db.query(ProductDrawing).filter(ProductDrawing.confirmed == 1, ProductDrawing.is_active == 1).all()
    rows = []
    for drawing in drawings:
        if not drawing.product_code:
            continue
        exists = (
            db.query(MaterialInventory.id)
            .filter(MaterialInventory.inventory_type == "product", MaterialInventory.material_code == drawing.product_code)
            .first()
        )
        if not exists:
            rows.append(
                {
                    "level": "中",
                    "type": "图纸未入库",
                    "object": drawing.product_code,
                    "quantity": 0,
                    "reason": "图纸已确认，但还没有对应产品库存记录",
                    "action": "如已生产完成，可从产品入库页面入库",
                }
            )
    return rows
