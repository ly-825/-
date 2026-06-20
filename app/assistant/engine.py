from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.inventory_agent import answer_inventory_assistant_with_context
from app.assistant.intent import parse_intent, serialize_context
from app.assistant.registry import dispatch
from app.assistant.safety import requires_write, write_block_response
from app.assistant.types import AssistantAction, AssistantResponse
from app.services.operation_log import record_operation_log


def run_assistant(message: str, context: str, db: Session) -> dict:
    text = message.strip()
    if not text:
        return AssistantResponse(answer="请输入你要查询的问题，例如：查 65Mn 板料库存、今天出库统计、图纸能不能改。", context=context).to_dict()

    if _is_help_request(text):
        return AssistantResponse(
            answer=(
                "我可以做只读查询和分析：库存汇总、库位查询、图纸参数、计划查料、出入库明细、异常预警和业务规则解释。"
                "我不会直接执行入库、出库、删除、撤销或修改。"
            ),
            context=context,
            actions=[
                AssistantAction("计划管理", "/admin/plans"),
                AssistantAction("库存查询", "/admin/inventory"),
                AssistantAction("图纸列表", "/admin/drawings"),
            ],
        ).to_dict()

    if requires_write(text):
        return write_block_response(context).to_dict()

    intent = parse_intent(text, context)
    if intent.get("safety", {}).get("requires_write"):
        return write_block_response(serialize_context(intent)).to_dict()

    response = dispatch(intent, db)
    if response:
        response.context = serialize_context(intent)
        record_operation_log(
            db,
            "assistant_query",
            str(intent.get("intent") or "unknown"),
            None,
            None,
            text,
            after_data={"intent": intent, "title": response.data.get("title") if response.data else None},
        )
        db.commit()
        return response.to_dict()

    if intent.get("intent") not in (None, "unknown", "help"):
        response = AssistantResponse(
            answer="这个问题我已经识别到了，但对应的工具还没有接入。你可以换一种问法，或先使用库存、图纸、流水、预警相关查询。",
            context=serialize_context(intent),
        )
        record_operation_log(db, "assistant_query", "missing_tool", None, None, text, after_data={"intent": intent})
        db.commit()
        return response.to_dict()

    fallback = answer_inventory_assistant_with_context(text, db, context)
    record_operation_log(db, "assistant_query", "fallback", None, None, text, after_data={"answer": fallback.get("answer")})
    db.commit()
    return fallback


def _is_help_request(text: str) -> bool:
    return any(keyword in text for keyword in ("帮助", "功能", "你能做什么", "怎么用", "能查什么", "会什么"))
