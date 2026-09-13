"""Declared recoverable conditions. Recovery is bounded, never a loop:
exhausting max_attempts promotes the condition to a hard failure.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.schema.common import Detector, Target
from src.schema.steps import ActionType

ThenAction = Literal["retry_step", "continue", "restart_from", "escalate"]


class RecoveryAction(BaseModel):
    action: ActionType | Literal["re_authenticate"]
    target: Target | None = None
    value: str | None = None


class Recovery(BaseModel):
    code: str
    kind: Literal["recoverable"] = "recoverable"
    description: str
    detect: Detector
    check_after: list[str]
    recovery: RecoveryAction
    max_attempts: int = Field(gt=0)
    then: ThenAction
    restart_step_id: str | None = None

    @model_validator(mode="after")
    def _check_restart_step_id(self) -> "Recovery":
        if self.then == "restart_from" and self.restart_step_id is None:
            raise ValueError("then='restart_from' requires restart_step_id")
        return self
