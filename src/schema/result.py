"""The replay result contract: what the engine returns. Exactly one of
outputs, outcome, or error is populated, matching `status`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, model_validator

ReplayStatus = Literal["success", "business_outcome", "failure"]


class ErrorDetail(BaseModel):
    code: str
    step_id: str | None = None
    expected: str
    observed: str
    strategy_used: str | None = None
    evidence: str | None = None


class OutcomeResult(BaseModel):
    code: str
    returns: dict[str, Any]


class RecoveryApplied(BaseModel):
    code: str
    step_id: str
    attempts: int


class HandoffRecord(BaseModel):
    """One escalate-claim-release cycle (docs/escalation-spec.md §6:
    "who, when, how long, how many actions").
    """

    claimed_by: str | None
    escalated_at: str | None
    claimed_at: str | None
    released_at: str | None
    duration_s: float | None
    action_count: int


class ReplayResult(BaseModel):
    status: ReplayStatus
    capability_id: str
    capability_version: str
    run_id: str

    outputs: dict[str, Any] | None = None
    outcome: OutcomeResult | None = None
    error: ErrorDetail | None = None

    recoveries_applied: list[RecoveryApplied] = []
    # Populated whenever the run ceded control to a human at least once,
    # regardless of how it ultimately ended (docs/escalation-spec.md §6).
    handoffs: list[HandoffRecord] = []
    # True only for the "human completed the flow manually" success path
    # (docs/escalation-spec.md §5): the engine detected the capability-level
    # success condition on reacquire and re-executed nothing.
    human_intervention: bool = False

    duration_ms: int
    steps_completed: int

    @model_validator(mode="after")
    def _check_status_matches_payload(self) -> "ReplayResult":
        populated = {
            "outputs": self.outputs is not None,
            "outcome": self.outcome is not None,
            "error": self.error is not None,
        }
        if sum(populated.values()) != 1:
            raise ValueError(
                "exactly one of outputs, outcome, error must be set, got: "
                f"{[k for k, v in populated.items() if v]}"
            )

        expected_field = {
            "success": "outputs",
            "business_outcome": "outcome",
            "failure": "error",
        }[self.status]
        if not populated[expected_field]:
            raise ValueError(
                f"status={self.status!r} requires {expected_field!r} to be set"
            )
        return self
