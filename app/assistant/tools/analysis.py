from __future__ import annotations

from sqlalchemy.orm import Session

from app.assistant.types import AssistantAction, AssistantIntent, AssistantResponse
from app.services import ai_analysis


HANDLERS = {
    "inventory_query": ai_analysis.inventory_top,
    "inventory_summary": ai_analysis.inventory_top,
    "inventory_ranking": ai_analysis.inventory_top,
    "inbound_summary": ai_analysis.transaction_summary,
    "outbound_summary": ai_analysis.transaction_summary,
    "inbound_ranking": ai_analysis.transaction_ranking,
    "outbound_ranking": ai_analysis.transaction_ranking,
    "transaction_summary": ai_analysis.transaction_summary,
    "scrap_idle_analysis": ai_analysis.scrap_idle,
    "scrap_ranking": ai_analysis.inventory_top,
    "drawing_recent": ai_analysis.drawing_recent,
    "drawing_version_ranking": ai_analysis.drawing_version_top,
    "loss_analysis": ai_analysis.loss_ranking,
    "warning_analysis": ai_analysis.warning_list,
    "comparison_analysis": ai_analysis.transaction_summary,
}


def run_analysis_tool(intent: AssistantIntent, db: Session) -> AssistantResponse | None:
    handler = HANDLERS.get(intent.get("intent"))
    if not handler:
        return None
    result = handler(intent, db)
    return AssistantResponse(
        answer=result.get("answer") or "已完成分析。",
        data=result.get("data"),
        actions=_actions_for_intent(intent),
    )


def _actions_for_intent(intent: AssistantIntent) -> list[AssistantAction]:
    entity = intent.get("entity")
    if entity == "raw_plate":
        return [AssistantAction("板料库存", "/admin/raw-plates"), AssistantAction("板料流水", "/admin/raw-plates/transactions")]
    if entity == "scrap":
        return [AssistantAction("余料记录", "/admin/scraps"), AssistantAction("余料流水", "/admin/scraps/transactions")]
    if entity == "drawing":
        return [AssistantAction("图纸列表", "/admin/drawings"), AssistantAction("待确认图纸", "/admin/drawings/pending")]
    return [AssistantAction("产品库存", "/admin/inventory"), AssistantAction("库存流水", "/admin/inventory/transactions")]

