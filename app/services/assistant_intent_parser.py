from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import re
from typing import Any

import requests

from app.config import settings


ALLOWED_INTENTS = {
    "inventory_query",
    "inventory_summary",
    "inventory_ranking",
    "inbound_summary",
    "inbound_ranking",
    "outbound_summary",
    "outbound_ranking",
    "transaction_query",
    "transaction_summary",
    "scrap_idle_analysis",
    "scrap_ranking",
    "drawing_query",
    "drawing_recent",
    "drawing_version_ranking",
    "loss_analysis",
    "warning_analysis",
    "comparison_analysis",
    "help",
    "unknown",
}
ALLOWED_ENTITIES = {"product", "raw_plate", "scrap", "drawing", "transaction", "inventory", "location", None}
ALLOWED_ACTIONS = {"query", "summary", "ranking", "analysis", "compare", "inbound", "outbound", "idle", "warning", None}
ALLOWED_METRICS = {"quantity", "inventory_quantity", "inbound_quantity", "outbound_quantity", "transaction_count", "ratio", "trend", "idle_days", "version_count", "loss_rate", "usage_rate", None}
WRITE_PATTERN = re.compile(r"(新增|新建|创建|修改|删除|移除|入库|出库|撤销|撤回|执行|运行|写入|更新|改成|改为|帮我.*出|帮我.*入)")
READ_PATTERN = re.compile(r"(查|查询|统计|分析|排行|排名|多少|哪些|有没有|报表|汇总|对比|趋势|最多|最少|最高|最低|Top|top|前)")


INTENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "entity", "action", "metric", "filters", "ranking", "time_range", "aggregation", "follow_up", "safety"],
    "properties": {
        "intent": {"type": "string", "enum": sorted(value for value in ALLOWED_INTENTS)},
        "entity": {"type": ["string", "null"], "enum": ["product", "raw_plate", "scrap", "drawing", "transaction", "inventory", "location", None]},
        "action": {"type": ["string", "null"], "enum": ["query", "summary", "ranking", "analysis", "compare", "inbound", "outbound", "idle", "warning", None]},
        "metric": {"type": ["string", "null"], "enum": ["quantity", "inventory_quantity", "inbound_quantity", "outbound_quantity", "transaction_count", "ratio", "trend", "idle_days", "version_count", "loss_rate", "usage_rate", None]},
        "filters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["product_code", "material", "thickness", "location", "status", "keyword"],
            "properties": {
                "product_code": {"type": ["string", "null"]},
                "material": {"type": ["string", "null"]},
                "thickness": {"type": ["number", "null"]},
                "location": {"type": ["string", "null"]},
                "status": {"type": ["string", "null"]},
                "keyword": {"type": ["string", "null"]},
            },
        },
        "ranking": {
            "type": "object",
            "additionalProperties": False,
            "required": ["enabled", "limit", "sort"],
            "properties": {
                "enabled": {"type": "boolean"},
                "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 100},
                "sort": {"type": ["string", "null"], "enum": ["asc", "desc", None]},
            },
        },
        "time_range": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "start_date", "end_date", "days"],
            "properties": {
                "type": {"type": ["string", "null"], "enum": ["today", "yesterday", "this_week", "this_month", "this_quarter", "this_year", "recent_days", "custom", None]},
                "start_date": {"type": ["string", "null"]},
                "end_date": {"type": ["string", "null"]},
                "days": {"type": ["integer", "null"], "minimum": 1, "maximum": 365},
            },
        },
        "aggregation": {
            "type": "object",
            "additionalProperties": False,
            "required": ["group_by", "include_total", "include_ratio", "include_trend"],
            "properties": {
                "group_by": {"type": "array", "items": {"type": "string", "enum": ["product_code", "material", "thickness", "location", "inventory_type", "date", "month"]}},
                "include_total": {"type": "boolean"},
                "include_ratio": {"type": "boolean"},
                "include_trend": {"type": "boolean"},
            },
        },
        "follow_up": {
            "type": "object",
            "additionalProperties": False,
            "required": ["is_follow_up", "inherits_context", "missing_fields"],
            "properties": {
                "is_follow_up": {"type": "boolean"},
                "inherits_context": {"type": "boolean"},
                "missing_fields": {"type": "array", "items": {"type": "string"}},
            },
        },
        "safety": {
            "type": "object",
            "additionalProperties": False,
            "required": ["read_only", "requires_write"],
            "properties": {
                "read_only": {"type": "boolean"},
                "requires_write": {"type": "boolean"},
            },
        },
    },
}


SYSTEM_PROMPT = """你是ERP/WMS/MES仓储业务意图解析器。你的任务是把用户自然语言解析成标准JSON Intent，不回答业务结果。
业务范围：DXF图纸、产品库存、板料库存、余料库存、FIFO、库存流水、入库统计、出库统计、损耗分析、智能预警。
必须只输出JSON对象，不能输出解释，不能生成SQL，不能编造数据库结果。
智能助手只读：如果用户要求新增、修改、删除、实际入库、实际出库、撤销、执行SQL或任何写操作，返回intent=unknown，safety.requires_write=true。
查询、统计、分析、对比、排名属于只读。
时间必须尽量解析为time_range.start_date和time_range.end_date。
排名表达如Top10、前十、前三、三个、最多、最少，必须输出ranking.limit和ranking.sort。
注意语义优先级：“库存出库最多的三个产品”是出库排名，不是库存排名；“库存最多的三个产品”才是库存排名。
多轮追问如“分别是多少”“明细呢”“那上个月呢”需要结合conversation_context继承上一轮entity、action、metric、ranking、time_range。
输出字段必须严格使用给定schema中的字段和值。"""


def default_intent() -> dict[str, Any]:
    return {
        "intent": "unknown",
        "entity": None,
        "action": None,
        "metric": None,
        "filters": {"product_code": None, "material": None, "thickness": None, "location": None, "status": None, "keyword": None},
        "ranking": {"enabled": False, "limit": None, "sort": None},
        "time_range": {"type": None, "start_date": None, "end_date": None, "days": None},
        "aggregation": {"group_by": [], "include_total": False, "include_ratio": False, "include_trend": False},
        "follow_up": {"is_follow_up": False, "inherits_context": False, "missing_fields": []},
        "safety": {"read_only": True, "requires_write": False},
    }


def parse_context(context: str | dict | None) -> dict[str, Any]:
    if isinstance(context, dict):
        return context
    if not context:
        return {}
    try:
        value = json.loads(context)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass
    parts = str(context).split("|", 4)
    if len(parts) >= 4 and parts[0] == "analysis":
        intent = default_intent()
        legacy_intent = parts[1]
        intent["intent"] = {
            "inventory_top": "inventory_ranking",
            "inventory_low": "inventory_ranking",
            "outbound_top": "outbound_ranking",
            "scrap_idle": "scrap_idle_analysis",
            "drawing_recent": "drawing_recent",
            "drawing_version_top": "drawing_version_ranking",
            "loss_ranking": "loss_analysis",
            "warning_list": "warning_analysis",
        }.get(legacy_intent, "unknown")
        intent["entity"] = parts[2] or None
        if "outbound" in intent["intent"]:
            intent["action"] = "outbound"
            intent["metric"] = "outbound_quantity"
        intent["ranking"] = {"enabled": True, "limit": int(parts[4]) if len(parts) == 5 and parts[4].isdigit() else 10, "sort": "desc"}
        return intent
    return {}


def chinese_number_to_int(value: str) -> int | None:
    table = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    if value.isdigit():
        return int(value)
    if value in table:
        return table[value]
    if value.startswith("十") and len(value) == 2:
        return 10 + table.get(value[1], 0)
    if value.endswith("十") and len(value) == 2:
        return table.get(value[0], 0) * 10
    if "十" in value and len(value) == 3:
        return table.get(value[0], 0) * 10 + table.get(value[2], 0)
    return None


def detect_limit(message: str, default: int | None = None) -> tuple[int | None, str | None]:
    patterns = [
        r"(?:Top|top)\s*(\d+)",
        r"前\s*(\d+|[一二两三四五六七八九十]{1,3})",
        r"(\d+|[一二两三四五六七八九十]{1,3})\s*个(?!月|星期|周|天|日)",
        r"(\d+|[一二两三四五六七八九十]{1,3})\s*项",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            number = chinese_number_to_int(match.group(1))
            if number:
                return min(max(number, 1), 100), "desc"
    if re.search(r"(最多|最高|热门|最快|排行|排名)", message):
        return default or 10, "desc"
    if re.search(r"(最少|最低)", message):
        return default or 10, "asc"
    return default, None


def detect_entity(message: str, fallback: str | None = None) -> str | None:
    entity_patterns = [
        ("raw_plate", r"(板料|钢板|原料)"),
        ("scrap", r"余料"),
        ("drawing", r"图纸"),
        ("transaction", r"(流水|记录)"),
        ("location", r"库位"),
        ("product", r"(产品|型号|成品)"),
        ("inventory", r"库存"),
    ]
    for entity, pattern in entity_patterns:
        if re.search(pattern, message):
            return entity
    return fallback


def parse_time_range(message: str, today: date | None = None, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    current = today or date.today()
    base = {"type": None, "start_date": None, "end_date": None, "days": None}
    if fallback:
        base.update({key: fallback.get(key) for key in base if fallback.get(key) is not None})
    if re.search(r"(今天|今日)", message):
        return {"type": "today", "start_date": current.isoformat(), "end_date": current.isoformat(), "days": 1}
    if re.search(r"昨天", message):
        target = current - timedelta(days=1)
        return {"type": "yesterday", "start_date": target.isoformat(), "end_date": target.isoformat(), "days": 1}
    if re.search(r"本周|这周|这个星期", message):
        start = current - timedelta(days=current.weekday())
        return {"type": "this_week", "start_date": start.isoformat(), "end_date": current.isoformat(), "days": (current - start).days + 1}
    if re.search(r"本月|这个月|这月", message):
        start = current.replace(day=1)
        return {"type": "this_month", "start_date": start.isoformat(), "end_date": current.isoformat(), "days": (current - start).days + 1}
    if re.search(r"本季度|这个季度|这季度", message):
        month = ((current.month - 1) // 3) * 3 + 1
        start = current.replace(month=month, day=1)
        return {"type": "this_quarter", "start_date": start.isoformat(), "end_date": current.isoformat(), "days": (current - start).days + 1}
    if re.search(r"今年|本年|这年", message):
        start = current.replace(month=1, day=1)
        return {"type": "this_year", "start_date": start.isoformat(), "end_date": current.isoformat(), "days": (current - start).days + 1}
    match = re.search(r"(?:最近|近|过去)\s*(\d+|[一二两三四五六七八九十]{1,3})\s*(?:个)?(天|日|周|星期|个月|月)", message)
    if match:
        amount = chinese_number_to_int(match.group(1)) or 1
        unit = match.group(2)
        days = amount * 7 if unit in ("周", "星期") else amount * 30 if unit in ("个月", "月") else amount
        start = current - timedelta(days=days)
        return {"type": "recent_days", "start_date": start.isoformat(), "end_date": current.isoformat(), "days": days}
    return base


def normalize_intent(raw: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or {}
    intent = default_intent()
    if isinstance(raw, dict):
        for key in intent:
            if key in raw and raw[key] is not None:
                if isinstance(intent[key], dict) and isinstance(raw[key], dict):
                    intent[key].update(raw[key])
                else:
                    intent[key] = raw[key]
    if intent["follow_up"].get("is_follow_up") or intent["follow_up"].get("inherits_context"):
        for key in ("entity", "action", "metric"):
            if intent.get(key) is None and previous.get(key) is not None:
                intent[key] = previous[key]
        for key in ("filters", "ranking", "time_range", "aggregation"):
            current_value = intent.get(key) or {}
            previous_value = previous.get(key) or {}
            if isinstance(current_value, dict) and isinstance(previous_value, dict):
                for sub_key, sub_value in previous_value.items():
                    if current_value.get(sub_key) in (None, [], ""):
                        current_value[sub_key] = sub_value
    if intent["intent"] not in ALLOWED_INTENTS:
        intent["intent"] = "unknown"
    if intent["entity"] not in ALLOWED_ENTITIES:
        intent["entity"] = None
    if intent["action"] not in ALLOWED_ACTIONS:
        intent["action"] = None
    if intent["metric"] not in ALLOWED_METRICS:
        intent["metric"] = None
    limit = intent["ranking"].get("limit")
    if limit is not None:
        try:
            intent["ranking"]["limit"] = min(max(int(limit), 1), 100)
        except (TypeError, ValueError):
            intent["ranking"]["limit"] = None
    if intent["ranking"].get("sort") not in ("asc", "desc", None):
        intent["ranking"]["sort"] = "desc"
    if intent["safety"].get("requires_write"):
        intent["intent"] = "unknown"
        intent["safety"]["read_only"] = False
    return intent


def fallback_parse_intent(message: str, context: str | dict | None = None, today: date | None = None) -> dict[str, Any]:
    previous = parse_context(context)
    intent = default_intent()
    intent["entity"] = detect_entity(message, previous.get("entity"))
    intent["time_range"] = parse_time_range(message, today=today, fallback=previous.get("time_range"))
    limit, sort = detect_limit(message)
    if limit:
        intent["ranking"] = {"enabled": True, "limit": limit, "sort": sort or "desc"}
    if previous and re.fullmatch(r"\s*(分别是多少|多少|明细|具体|列表|有哪些|哪些)\s*", message):
        intent["follow_up"] = {"is_follow_up": True, "inherits_context": True, "missing_fields": []}
        return normalize_intent(intent, previous)
    asks_write = WRITE_PATTERN.search(message) and not READ_PATTERN.search(message)
    if asks_write:
        intent["safety"] = {"read_only": False, "requires_write": True}
        return normalize_intent(intent, previous)
    has_outbound = re.search(r"(出库|出了|领用|消耗)", message)
    has_inbound = re.search(r"(入库|入了|进货|收货)", message)
    has_inventory = re.search(r"库存", message)
    has_idle = re.search(r"(未使用|没使用|长期|积压|超过\s*(\d+|[一二两三四五六七八九十]{1,3})\s*天)", message)
    if has_outbound and intent["ranking"].get("enabled"):
        intent["intent"] = "outbound_ranking"
        intent["action"] = "outbound"
        intent["metric"] = "outbound_quantity"
        intent["entity"] = "product" if intent["entity"] in (None, "inventory") else intent["entity"]
    elif has_outbound:
        intent["intent"] = "outbound_summary"
        intent["action"] = "outbound"
        intent["metric"] = "outbound_quantity"
        intent["entity"] = "product" if intent["entity"] in (None, "inventory") else intent["entity"]
    elif has_inbound and intent["ranking"].get("enabled"):
        intent["intent"] = "inbound_ranking"
        intent["action"] = "inbound"
        intent["metric"] = "inbound_quantity"
    elif has_inbound:
        intent["intent"] = "inbound_summary"
        intent["action"] = "inbound"
        intent["metric"] = "inbound_quantity"
    elif intent["entity"] == "scrap" and has_idle:
        days_match = re.search(r"超过\s*(\d+|[一二两三四五六七八九十]{1,3})\s*天", message)
        days = chinese_number_to_int(days_match.group(1)) if days_match else None
        intent["intent"] = "scrap_idle_analysis"
        intent["action"] = "idle"
        intent["metric"] = "idle_days"
        intent["time_range"]["days"] = days or intent["time_range"].get("days") or 30
    elif intent["entity"] == "drawing" and intent["ranking"].get("enabled"):
        intent["intent"] = "drawing_version_ranking"
        intent["action"] = "ranking"
        intent["metric"] = "version_count"
    elif intent["entity"] == "drawing":
        intent["intent"] = "drawing_recent"
        intent["action"] = "query"
        intent["metric"] = "quantity"
        intent["time_range"]["days"] = intent["time_range"].get("days") or 30
    elif re.search(r"(损耗|利用率|差异率)", message):
        intent["intent"] = "loss_analysis"
        intent["entity"] = "product"
        intent["action"] = "analysis"
        intent["metric"] = "loss_rate"
    elif re.search(r"(预警|风险|快缺货|不足)", message):
        intent["intent"] = "warning_analysis"
        intent["action"] = "warning"
        intent["metric"] = "quantity"
    elif has_inventory and intent["ranking"].get("enabled"):
        intent["intent"] = "inventory_ranking"
        intent["action"] = "ranking"
        intent["metric"] = "inventory_quantity"
        intent["entity"] = "product" if intent["entity"] in (None, "inventory") else intent["entity"]
    elif has_inventory:
        intent["intent"] = "inventory_summary"
        intent["action"] = "summary"
        intent["metric"] = "inventory_quantity"
        intent["entity"] = "product" if intent["entity"] in (None, "inventory") else intent["entity"]
    return normalize_intent(intent, previous)


def call_llm_intent_parser(message: str, context: dict[str, Any]) -> dict[str, Any] | None:
    if not settings.dashscope_api_key:
        return None
    payload = {
        "model": settings.qwen_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"current_date": date.today().isoformat(), "message": message, "conversation_context": context, "json_schema": INTENT_SCHEMA}, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    response = requests.post(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.dashscope_api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return parsed if isinstance(parsed, dict) else None


def parse_assistant_intent(message: str, context: str | dict | None = None) -> dict[str, Any]:
    previous = parse_context(context)
    try:
        parsed = call_llm_intent_parser(message, previous)
        if parsed:
            return normalize_intent(parsed, previous)
    except Exception:
        pass
    return fallback_parse_intent(message, previous)


def serialize_intent_context(intent: dict[str, Any]) -> str:
    return json.dumps(intent, ensure_ascii=False, separators=(",", ":"))
