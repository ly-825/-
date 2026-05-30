from __future__ import annotations

from sqlalchemy.orm import Session

from app.assistant.render import table
from app.assistant.types import AssistantAction, AssistantIntent, AssistantResponse
from app.models import MaterialInventory


def query_location(intent: AssistantIntent, db: Session) -> AssistantResponse:
    filters = intent.get("filters") or {}
    keyword = (filters.get("location") or filters.get("keyword") or "").strip()
    query = db.query(MaterialInventory).filter(MaterialInventory.quantity > 0)
    if keyword:
        query = query.filter(MaterialInventory.location.ilike(f"%{keyword}%"))
    items = query.order_by(MaterialInventory.inventory_type.asc(), MaterialInventory.location.asc(), MaterialInventory.updated_at.desc()).limit(100).all()
    rows = [
        {
            "type": {"product": "产品", "raw_plate": "板料", "scrap": "余料"}.get(item.inventory_type, item.inventory_type),
            "code": item.material_code or item.source_product_code or "-",
            "material": item.material,
            "size": item.usable_size or f"{item.length or '-'}×{item.width or '-'}×{item.thickness or '-'}",
            "quantity": item.quantity,
            "location": item.location or "-",
        }
        for item in items
    ]
    title = f"{keyword or '全部库位'}库存清单"
    return AssistantResponse(
        answer=f"查到 {len(rows)} 条库位库存记录。",
        data=table(
            title,
            [
                {"prop": "type", "label": "类型"},
                {"prop": "code", "label": "编号/来源"},
                {"prop": "material", "label": "材质"},
                {"prop": "size", "label": "尺寸"},
                {"prop": "quantity", "label": "数量"},
                {"prop": "location", "label": "库位"},
            ],
            rows,
        ),
        actions=[
            AssistantAction("产品库存", "/admin/inventory"),
            AssistantAction("板料库存", "/admin/raw-plates"),
            AssistantAction("余料记录", "/admin/scraps"),
        ],
    )

