from __future__ import annotations

import re

from app.assistant.types import AssistantAction, AssistantResponse


WRITE_PATTERN = re.compile(r"(新增|新建|创建|修改|删除|移除|入库|出库|撤销|撤回|执行|运行|写入|更新|改成|改为|清空|覆盖|帮我.*出|帮我.*入)")
READ_PATTERN = re.compile(r"(查|查询|统计|分析|排行|排名|多少|哪些|有没有|报表|汇总|对比|趋势|最多|最少|最高|最低|Top|top|前|明细|规则|为什么|怎么|谁|操作人|记录|列表)")


def requires_write(message: str) -> bool:
    return bool(WRITE_PATTERN.search(message) and not READ_PATTERN.search(message))


def write_block_response(context: str = "") -> AssistantResponse:
    return AssistantResponse(
        answer="智能助手当前只支持查询、分析和规则解释，不能直接执行新增、修改、删除、入库、出库或撤销操作。",
        context=context,
        actions=[
            AssistantAction("产品入库", "/admin/inventory/inbound"),
            AssistantAction("产品出库", "/admin/inventory/outbound"),
            AssistantAction("余料出库", "/admin/scraps/outbound"),
            AssistantAction("操作日志", "/admin/operation-logs"),
        ],
    )
