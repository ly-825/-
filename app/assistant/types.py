from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AssistantAction:
    label: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "url": self.url}


@dataclass
class AssistantResponse:
    answer: str
    context: str = ""
    data: dict[str, Any] | None = None
    actions: list[AssistantAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"answer": self.answer, "context": self.context}
        if self.data is not None:
            payload["data"] = self.data
        if self.actions:
            payload["actions"] = [action.to_dict() for action in self.actions]
        return payload


AssistantIntent = dict[str, Any]

