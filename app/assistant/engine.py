from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.inventory_agent import answer_inventory_assistant_with_context
from app.assistant.intent import parse_intent, serialize_context
from app.assistant.registry import dispatch
from app.assistant.safety import requires_write, write_block_response
from app.assistant.types import AssistantResponse
from app.services.operation_log import record_operation_log


def run_assistant(message: str, context: str, db: Session) -> dict:
    text = message.strip()
    if not text:
        return AssistantResponse(answer="请输入你要查询的问题，例如：查 65Mn 板料库存、今天出库统计、图纸能不能改。", context=context).to_dict()

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
