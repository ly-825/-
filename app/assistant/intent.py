from __future__ import annotations

import re

from app.assistant.types import AssistantIntent
from app.assistant.tools.drawing import detect_drawing_parameter
from app.services.assistant_intent_parser import default_intent, fallback_parse_intent, serialize_intent_context


def parse_intent(message: str, context: str | dict | None = None) -> AssistantIntent:
    text = message.strip()
    if re.search(r"(FIFO|先进先出)", text, re.IGNORECASE):
        return _rule_intent("fifo", context)
    if re.search(r"(撤回|撤销|反撤)", text):
        return _rule_intent("reverse", context)
    if "图纸" in text and re.search(r"(能不能|是否|可以|修改|删除|重新识别|改|删|规则)", text):
        return _rule_intent("drawing", context)
    drawing_parameter = detect_drawing_parameter(text)
    if ("图纸" in text and (re.search(r"(按|根据|参数|列出|呈现|显示|打印)", text) or drawing_parameter)) or (
        "产品" in text and drawing_parameter and "库存" not in text
    ):
        intent = fallback_parse_intent(message, context)
        intent["intent"] = "drawing_parameter_list"
        intent["entity"] = "drawing"
        intent["action"] = "query"
        intent["safety"] = {"read_only": True, "requires_write": False}
        intent["_message"] = text
        return intent
    if "余料" in text and re.search(r"(怎么生成|如何生成|为什么|数量|产品入库|规则)", text):
        return _rule_intent("scrap_generation", context)
    if "库位" in text and re.search(r"(有什么|有哪些|查|查询|多少|库存)", text):
        intent = fallback_parse_intent(message, context)
        intent["intent"] = "location_query"
        intent["entity"] = "location"
        intent["_message"] = text
        return intent
    if re.search(r"(预警|异常|风险|待处理|待办|提醒|快缺货|不足)", text):
        intent = fallback_parse_intent(message, context)
        intent["intent"] = "warning_list"
        intent["action"] = "warning"
        intent["_message"] = text
        return intent
    if re.search(r"(明细|哪些|列表|谁|操作人|记录)", text) and re.search(r"(入库|出库|出了|入了|领用|收货)", text):
        intent = fallback_parse_intent(message, context)
        intent["intent"] = "transaction_detail"
        if re.search(r"(入库|入了|收货)", text):
            intent["action"] = "inbound"
        elif re.search(r"(出库|出了|领用)", text):
            intent["action"] = "outbound"
        if re.search(r"(板料|钢板|原料)", text):
            intent["entity"] = "raw_plate"
        elif "余料" in text:
            intent["entity"] = "scrap"
        elif re.search(r"(产品|型号|成品)", text):
            intent["entity"] = "product"
        intent["safety"] = {"read_only": True, "requires_write": False}
        intent["_message"] = text
        return intent
    intent = fallback_parse_intent(message, context)
    if "图纸" in text and re.search(r"(查|查询|有没有|哪些|列表|明细)", text):
        intent["intent"] = "drawing_query"
        intent["entity"] = "drawing"
    if re.search(r"(查|查询|有没有|还有多少|多少|库存|列表|明细)", text):
        if re.search(r"(板料|钢板|原料)", text):
            intent["intent"] = "inventory_query"
            intent["entity"] = "raw_plate"
        elif "余料" in text:
            intent["intent"] = "inventory_query"
            intent["entity"] = "scrap"
        elif re.search(r"(产品|型号|成品|库存)", text) and intent.get("intent") in ("unknown", "inventory_summary", "inventory_query", None):
            intent["intent"] = "inventory_query"
            intent["entity"] = "product" if intent.get("entity") in (None, "inventory") else intent.get("entity")
    intent["_message"] = text
    return intent


def serialize_context(intent: AssistantIntent) -> str:
    return serialize_intent_context(intent)


def _rule_intent(rule: str, context: str | dict | None) -> AssistantIntent:
    intent = default_intent()
    intent["intent"] = "rule_explain"
    intent["entity"] = rule
    intent["action"] = "query"
    intent["metric"] = None
    return intent
