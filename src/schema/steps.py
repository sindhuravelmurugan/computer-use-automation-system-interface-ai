"""The ordered flow: the small, closed action vocabulary the discovery agent
can emit, and the checkpoints/waits that keep a step from failing silently.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

from src.schema.common import Detector, Target, WaitSpec

ActionType = Literal["navigate", "click", "type", "select", "read", "wait_for", "assert"]
RiskLevel = Literal["safe", "risky"]
OnTimeout = Literal["fail", "escalate"]

_ACTIONS_REQUIRING_TARGET: frozenset[ActionType] = frozenset(
    {"click", "type", "select", "read", "assert"}
)


class Step(BaseModel):
    id: str
    action: ActionType
    target: Target | None = None
    value: str | None = None
    risk: RiskLevel = "safe"
    checkpoint: Detector | None = None
    wait: WaitSpec | None = None
    on_timeout: OnTimeout = "fail"
    notes: str | None = None

    @model_validator(mode="after")
    def _check_action_shape(self) -> "Step":
        if self.action in _ACTIONS_REQUIRING_TARGET and self.target is None:
            raise ValueError(f"action {self.action!r} requires a target")
        if self.action == "navigate" and not self.value:
            raise ValueError("action 'navigate' requires a value (the URL)")
        if self.action == "wait_for" and self.wait is None:
            raise ValueError("action 'wait_for' requires a wait spec")
        return self
