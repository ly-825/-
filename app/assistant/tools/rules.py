from __future__ import annotations

from sqlalchemy.orm import Session

from app.assistant.types import AssistantAction, AssistantIntent, AssistantResponse


def explain_rule(intent: AssistantIntent, db: Session) -> AssistantResponse:
    rule = intent.get("entity")
    if rule == "drawing":
        return AssistantResponse(
            answer=(
                "图纸规则：未产生库存或余料记录前，可以修改、删除、重新识别；"
                "一旦产品入库或生成余料后，旧图纸不能直接覆盖，应该上传并确认新版本。"
            ),
            actions=[AssistantAction("待确认图纸", "/admin/drawings/pending"), AssistantAction("已确认图纸", "/admin/drawings/confirmed")],
        )
    if rule == "reverse":
        return AssistantResponse(
            answer=(
                "撤销规则：系统不会删除原流水，而是生成一条反向流水。"
                "入库撤销需要当前库存足够扣回；出库撤销会加回原批次；同一流水不能重复撤销。"
            ),
            actions=[AssistantAction("产品流水", "/admin/inventory/transactions"), AssistantAction("余料流水", "/admin/scraps/transactions")],
        )
    if rule == "fifo":
        return AssistantResponse(
            answer=(
                "FIFO 规则：先进先出。出库时系统优先扣减最早入库且仍有库存的批次；"
                "如果一个批次数量不够，会继续扣下一个批次，并分别生成流水。"
            ),
            actions=[AssistantAction("产品出库", "/admin/inventory/outbound"), AssistantAction("板料出库", "/admin/raw-plates/outbound")],
        )
    if rule == "scrap_generation":
        return AssistantResponse(
            answer=(
                "余料生成规则：产品入库数量是多少，就自动生成同数量的待确认余料。"
                "这些余料先进入“待入库余料”，需要人工确认实际数量、实际直径和库位后，才会变成可用余料。"
                "产品出库不会自动扣余料，余料出库是单独流程。"
            ),
            actions=[AssistantAction("产品入库", "/admin/inventory/inbound"), AssistantAction("待入库余料", "/admin/scraps/pending")],
        )
    return AssistantResponse(
        answer="我可以解释图纸修改、FIFO、流水撤销、产品入库后余料生成等规则。",
        actions=[AssistantAction("智能助手", "/admin/assistant")],
    )

