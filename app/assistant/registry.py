from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.assistant.tools.inventory import query_drawings, query_inventory
from app.assistant.tools.analysis import run_analysis_tool
from app.assistant.tools.location import query_location
from app.assistant.tools.rules import explain_rule
from app.assistant.tools.transaction import query_transactions
from app.assistant.tools.warning import warning_list
from app.assistant.types import AssistantIntent, AssistantResponse

Tool = Callable[[AssistantIntent, Session], AssistantResponse | None]


TOOLS: dict[str, Tool] = {
    "rule_explain": explain_rule,
    "location_query": query_location,
    "inventory_query": query_inventory,
    "inventory_summary": query_inventory,
    "drawing_query": query_drawings,
    "transaction_detail": query_transactions,
    "warning_list": warning_list,
    "warning_analysis": warning_list,
}


def dispatch(intent: AssistantIntent, db: Session) -> AssistantResponse | None:
    tool = TOOLS.get(str(intent.get("intent")))
    if tool:
        return tool(intent, db)
    return run_analysis_tool(intent, db)
