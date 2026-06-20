from datetime import datetime, timedelta
from dataclasses import dataclass
import re

from sqlalchemy.orm import Session

from app.models import InventoryTransactionRecord, MaterialInventory, ProductDrawing
from app.services.inventory_service import drawing_has_inventory_references
from app.time_utils import china_now


@dataclass
class AssistantIntent:
    name: str
    domain: str | None = None
    period: str | None = None
    query: str = ""
    flow: str | None = None

    def to_context(self) -> str:
        return f"{self.name}|{self.domain or ''}|{self.period or ''}|{self.flow or ''}|{self.query}"

    @staticmethod
    def from_context(value: str) -> "AssistantIntent | None":
        parts = (value or "").split("|", 4)
        if len(parts) == 4 and parts[0]:
            return AssistantIntent(parts[0], parts[1] or None, parts[2] or None, parts[3])
        if len(parts) != 5 or not parts[0]:
            return None
        return AssistantIntent(parts[0], parts[1] or None, parts[2] or None, parts[4], parts[3] or None)


def extract_query(message: str, domain: str | None) -> str:
    text = message.strip()
    phrases = (
        "查一下", "还有多少", "能不能", "重新识别", "什么意思",
        "这个月", "本个月", "今天", "今日", "本月", "这月", "今年", "本年", "这年",
        "查询", "库存", "有没有", "多少", "几个", "几件", "几张", "帮我", "请", "一下", "情况", "数量",
        "统计", "报表", "出库", "出了", "领用", "入库", "是否", "可以", "修改", "删除", "撤回", "撤销", "规则", "是什么", "查",
    )
    for phrase in phrases:
        text = text.replace(phrase, " ")
    if domain == "raw_plate":
        for word in ("板料", "钢板", "原料"):
            text = text.replace(word, " ")
    elif domain == "scrap":
        text = text.replace("余料", " ")
    elif domain == "product":
        for word in ("产品", "型号", "成品"):
            text = text.replace(word, " ")
    elif domain == "drawing":
        text = text.replace("图纸", " ")
    return " ".join(part.strip(" ，,。？?：:") for part in text.split() if part.strip(" ，,。？?：:"))


def recognize_intent(message: str, context: str = "") -> AssistantIntent:
    text = message.strip()
    previous = AssistantIntent.from_context(context)
    period = None
    if re.search(r"(今天|今日)", text):
        period = "day"
    elif re.search(r"(本月|这个月|本个月|这月)", text):
        period = "month"
    elif re.search(r"(今年|本年|这年)", text):
        period = "year"

    flow = None
    if re.search(r"(入库|入了|进货|收货)", text):
        flow = "in"
    elif re.search(r"(出库|出了|领用)", text):
        flow = "out"

    domain = None
    if re.search(r"(板料|钢板|原料)", text):
        domain = "raw_plate"
    elif "余料" in text:
        domain = "scrap"
    elif "图纸" in text:
        domain = "drawing"
    elif re.search(r"(产品|型号|成品)", text):
        domain = "product"

    query = extract_query(text, domain)
    if previous and previous.name in ("transaction_statistics", "outbound_statistics", "inbound_statistics") and re.search(r"(分别|哪些|明细|型号|列表|具体)", text):
        previous_flow = previous.flow or ("out" if previous.name == "outbound_statistics" else "in" if previous.name == "inbound_statistics" else None)
        return AssistantIntent("transaction_details", domain=previous.domain, period=previous.period, query=query, flow=previous_flow)
    if flow and re.search(r"(多少|几|统计|报表|数量)", text):
        return AssistantIntent("transaction_statistics", domain=domain, period=period or "day", query=query, flow=flow)
    if period and re.search(r"(统计|报表|多少|数量)", text):
        return AssistantIntent("transaction_statistics", domain=domain, period=period, query=query, flow=None)
    if domain == "drawing" and re.search(r"(能不能|是否|可以|修改|删除|重新识别|改|删)", text):
        return AssistantIntent("explain_drawing_rule", domain=domain, period=period, query=query)
    if re.search(r"(撤回|撤销|反撤)", text):
        return AssistantIntent("explain_reverse_rule", domain=domain, period=period, query=query)
    if re.search(r"(FIFO|先进先出)", text, re.IGNORECASE):
        return AssistantIntent("explain_fifo_rule", domain=domain, period=period, query=query)
    if domain == "drawing":
        return AssistantIntent("search_drawings", domain=domain, period=period, query=query)
    if domain == "raw_plate":
        return AssistantIntent("search_raw_plate_inventory", domain=domain, period=period, query=query)
    if domain == "scrap":
        return AssistantIntent("search_scrap_inventory", domain=domain, period=period, query=query)
    if re.search(r"(帮助|怎么用|规则|说明)", text):
        return AssistantIntent("help", domain=domain, period=period, query=query)
    return AssistantIntent("search_product_inventory", domain="product", period=period, query=extract_query(text, "product"))


def _fmt_num(value: float | int | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _like_query(query, keyword: str):
    like = f"%{keyword}%"
    return query.filter(
        (MaterialInventory.material_code.ilike(like))
        | (MaterialInventory.material.ilike(like))
        | (MaterialInventory.location.ilike(like))
        | (MaterialInventory.usable_size.ilike(like))
        | (MaterialInventory.source_product_code.ilike(like))
    )


def search_product_inventory(intent: AssistantIntent, db: Session) -> str:
    keyword = intent.query
    query = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "product", MaterialInventory.quantity > 0)
    if keyword:
        query = _like_query(query, keyword)
    items = query.order_by(MaterialInventory.updated_at.desc()).limit(20).all()
    if not items:
        return f"没有查到与“{keyword}”相关的产品库存。"
    grouped: dict[str, dict] = {}
    for item in items:
        code = item.material_code or item.source_product_code or "未编号"
        group = grouped.setdefault(code, {"quantity": 0, "material": item.material, "thickness": item.thickness, "locations": set()})
        group["quantity"] += item.quantity
        if item.location:
            group["locations"].add(item.location)
    lines = [f"查到 {len(grouped)} 个产品库存汇总："]
    for code, group in list(grouped.items())[:10]:
        lines.append(f"- {code}：{group['quantity']} 件，材质 {group['material']}，厚度 {_fmt_num(group['thickness'])}，库位 {' / '.join(sorted(group['locations'])) or '-'}")
    lines.append("可到 /admin/inventory 查看更多明细。")
    return "\n".join(lines)


def search_raw_plate_inventory(intent: AssistantIntent, db: Session) -> str:
    keyword = intent.query
    query = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "raw_plate", MaterialInventory.quantity > 0)
    if keyword:
        _like = f"%{keyword}%"
        query = query.filter((MaterialInventory.material.ilike(_like)) | (MaterialInventory.material_code.ilike(_like)) | (MaterialInventory.location.ilike(_like)) | (MaterialInventory.usable_size.ilike(_like)))
    items = query.order_by(MaterialInventory.created_at.asc()).limit(30).all()
    if not items:
        return f"没有查到与“{keyword}”相关的板料库存。"
    grouped: dict[tuple, dict] = {}
    for item in items:
        key = (item.material, item.length, item.width, item.thickness)
        group = grouped.setdefault(key, {"quantity": 0, "batches": 0, "locations": set()})
        group["quantity"] += item.quantity
        group["batches"] += 1
        if item.location:
            group["locations"].add(item.location)
    lines = [f"查到 {len(grouped)} 个板料规格汇总："]
    for (material, length, width, thickness), group in list(grouped.items())[:10]:
        lines.append(f"- {material} {_fmt_num(length)}×{_fmt_num(width)}×{_fmt_num(thickness)}mm：{group['quantity']} 张，{group['batches']} 个批次，库位 {' / '.join(sorted(group['locations'])) or '-'}")
    lines.append("板料出库会按 FIFO 从最早入库批次扣减。")
    return "\n".join(lines)


def search_scrap_inventory(intent: AssistantIntent, db: Session) -> str:
    keyword = intent.query
    query = db.query(MaterialInventory).filter(MaterialInventory.inventory_type == "scrap", MaterialInventory.status == "available", MaterialInventory.quantity > 0)
    if keyword:
        query = _like_query(query, keyword)
    items = query.order_by(MaterialInventory.diameter.asc(), MaterialInventory.created_at.asc()).limit(20).all()
    if not items:
        return f"没有查到与“{keyword}”相关的可用余料。"
    lines = [f"查到 {len(items)} 条可用余料："]
    for item in items[:10]:
        lines.append(f"- {item.material}，厚度 {_fmt_num(item.thickness)}，尺寸 {item.usable_size or '-'}，数量 {item.quantity}，库位 {item.location or '-'}")
    return "\n".join(lines)


def search_drawings(intent: AssistantIntent, db: Session) -> str:
    keyword = intent.query
    query = db.query(ProductDrawing)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            (ProductDrawing.product_code.ilike(like))
            | (ProductDrawing.product_name.ilike(like))
            | (ProductDrawing.product_category.ilike(like))
            | (ProductDrawing.remark.ilike(like))
            | (ProductDrawing.material.ilike(like))
        )
    drawings = query.order_by(ProductDrawing.updated_at.desc()).limit(10).all()
    if not drawings:
        return f"没有查到与“{keyword}”相关的图纸。"
    lines = [f"查到 {len(drawings)} 张图纸："]
    for drawing in drawings:
        locked = drawing_has_inventory_references(drawing.id, db)
        lines.append(f"- ID {drawing.id}｜{drawing.product_code or '-'}｜{drawing.product_category or '-'}｜A{drawing.version or 1}｜备注：{drawing.remark or '-'}｜{'已确认' if drawing.confirmed else '未确认'}｜{'已使用，不可直接修改' if locked else '未使用，可修改/重识别/删除'}")
    return "\n".join(lines)


def outbound_statistics(intent: AssistantIntent, db: Session) -> str:
    transaction_type = intent.flow or "out"
    action_label = "入库" if transaction_type == "in" else "出库"
    now = china_now()
    start = datetime(now.year, now.month, now.day)
    label = "今天"
    if intent.period == "month":
        start = datetime(now.year, now.month, 1)
        label = "本月"
    elif intent.period == "year":
        start = datetime(now.year, 1, 1)
        label = "本年"
    end = now + timedelta(days=1)
    records = db.query(InventoryTransactionRecord).filter(
        InventoryTransactionRecord.transaction_type == transaction_type,
        InventoryTransactionRecord.reversed_transaction_id.is_(None),
        InventoryTransactionRecord.created_at >= start,
        InventoryTransactionRecord.created_at < end,
    ).all()
    inventory_ids = [record.inventory_id for record in records]
    inventory_map = {item.id: item for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()} if inventory_ids else {}
    totals = {"product": 0, "scrap": 0, "raw_plate": 0}
    for record in records:
        item = inventory_map.get(record.inventory_id)
        if item and item.inventory_type in totals:
            totals[item.inventory_type] += record.quantity
    if intent.domain == "product":
        return f"{label}产品{action_label}：{totals['product']} 件\n可到 /admin/inventory/transactions 查看明细。"
    if intent.domain == "scrap":
        return f"{label}余料{action_label}：{totals['scrap']} 件\n可到 /admin/scraps/transactions 查看明细。"
    if intent.domain == "raw_plate":
        return f"{label}板料{action_label}：{totals['raw_plate']} 张\n可到 /admin/raw-plates/transactions 查看明细。"
    return f"{label}{action_label}统计：\n- 产品：{totals['product']} 件\n- 余料：{totals['scrap']} 件\n- 板料：{totals['raw_plate']} 张"


def outbound_details(intent: AssistantIntent, db: Session) -> str:
    transaction_type = intent.flow or "out"
    action_label = "入库" if transaction_type == "in" else "出库"
    start_label_intent = AssistantIntent("outbound_statistics", domain=intent.domain, period=intent.period)
    now = china_now()
    if start_label_intent.period == "month":
        start, label = datetime(now.year, now.month, 1), "本月"
    elif start_label_intent.period == "year":
        start, label = datetime(now.year, 1, 1), "本年"
    else:
        start, label = datetime(now.year, now.month, now.day), "今天"
    end = now + timedelta(days=1)
    records = db.query(InventoryTransactionRecord).filter(
        InventoryTransactionRecord.transaction_type == transaction_type,
        InventoryTransactionRecord.reversed_transaction_id.is_(None),
        InventoryTransactionRecord.created_at >= start,
        InventoryTransactionRecord.created_at < end,
    ).all()
    inventory_ids = [record.inventory_id for record in records]
    inventory_map = {item.id: item for item in db.query(MaterialInventory).filter(MaterialInventory.id.in_(inventory_ids)).all()} if inventory_ids else {}
    grouped: dict[str, int] = {}
    for record in records:
        item = inventory_map.get(record.inventory_id)
        if not item:
            continue
        if intent.domain and item.inventory_type != intent.domain:
            continue
        if intent.domain == "product":
            key = item.material_code or item.source_product_code or "未编号"
        elif intent.domain == "raw_plate":
            key = f"{item.material} {_fmt_num(item.length)}×{_fmt_num(item.width)}×{_fmt_num(item.thickness)}mm"
        elif intent.domain == "scrap":
            key = f"{item.material} {item.usable_size or '-'}"
        else:
            key = item.material_code or item.source_product_code or item.material or "未编号"
        grouped[key] = grouped.get(key, 0) + record.quantity
    if not grouped:
        return f"{label}没有对应{action_label}明细。"
    unit = "件" if intent.domain != "raw_plate" else "张"
    lines = [f"{label}{action_label}明细："]
    for key, quantity in sorted(grouped.items(), key=lambda item: item[0])[:20]:
        lines.append(f"- {key}：{quantity} {unit}")
    return "\n".join(lines)


def explain_rules(message: str) -> str:
    if "图纸" in message:
        return "图纸规则：未产生库存/余料记录前可以修改、删除、重新识别；一旦已被产品库存、余料库存或余料生成记录引用，就不能直接覆盖，只能上传并确认新版本。"
    if "撤" in message:
        return "撤回规则：系统不会删除原流水，而是生成一条反向流水；入库撤回需要当前库存足够扣回，出库撤回会加回原批次；同一流水不能重复撤回。"
    if "FIFO" in message.upper() or "先进先出" in message:
        return "FIFO 规则：先进先出。出库时系统优先扣减最早入库且仍有库存的批次；如果一个批次数量不够，会继续扣下一个批次，并分别生成流水。"
    if "板料" in message:
        return "板料规则：入库按总重量、长宽厚和密度换算张数；出库按同规格 FIFO 扣减；已有出库流水的批次只允许修改批次号和库位。"
    return "我目前是只读助手，可以查询产品、板料、余料、图纸和出库统计，也可以解释图纸修改、FIFO、流水撤回等规则。"


def answer_inventory_assistant_with_context(message: str, db: Session, context: str = "") -> dict[str, str]:
    text = message.strip()
    if not text:
        return {"answer": "请输入你要查询的问题，例如：查 65Mn 板料库存、今天出库统计、这个图纸能不能改。", "context": context}
    intent = recognize_intent(text, context)
    if intent.name in ("transaction_statistics", "outbound_statistics", "inbound_statistics"):
        return {"answer": outbound_statistics(intent, db), "context": intent.to_context()}
    if intent.name in ("transaction_details", "outbound_details", "inbound_details"):
        return {"answer": outbound_details(intent, db), "context": intent.to_context()}
    if intent.name == "explain_drawing_rule":
        return {"answer": explain_rules("图纸") + "\n\n" + search_drawings(intent, db), "context": intent.to_context()}
    if intent.name == "search_drawings":
        return {"answer": search_drawings(intent, db), "context": intent.to_context()}
    if intent.name == "explain_reverse_rule":
        return {"answer": explain_rules("撤回"), "context": intent.to_context()}
    if intent.name == "explain_fifo_rule":
        return {"answer": explain_rules("FIFO"), "context": intent.to_context()}
    if intent.name == "search_raw_plate_inventory":
        return {"answer": search_raw_plate_inventory(intent, db), "context": intent.to_context()}
    if intent.name == "search_scrap_inventory":
        return {"answer": search_scrap_inventory(intent, db), "context": intent.to_context()}
    if intent.name == "help":
        return {"answer": explain_rules(text), "context": intent.to_context()}
    return {"answer": search_product_inventory(intent, db), "context": intent.to_context()}


def answer_inventory_assistant(message: str, db: Session) -> str:
    return answer_inventory_assistant_with_context(message, db)["answer"]
