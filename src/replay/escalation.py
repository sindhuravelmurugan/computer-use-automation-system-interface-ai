"""Escalation (docs/replay-spec.md §6). Raising an InterventionRequest is
unconditional whenever a recovery exhausts max_attempts or directs
`then: "escalate"` — a run that hit something it couldn't resolve must leave
something a human reviews. Whether the session stays open for takeover is a
*separate* decision (the table in §6), so the two are kept as distinct steps
here rather than folded into one branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from src.replay.evidence import EvidenceWriter
from src.surface.types import PageSignature, Rect

if TYPE_CHECKING:
    from src.replay.context import ReplayContext
    from src.surface.protocol import Surface

EscalationReason = Literal["EXHAUSTED_RECOVERY", "ESCALATE_DIRECTIVE", "POLICY_REQUIRES_APPROVAL"]


@dataclass
class InterventionRequest:
    run_id: str
    capability_id: str
    capability_version: str
    step_id: str
    reason: str
    page: PageSignature
    screenshot_path: str
    session_held: bool
    raised_at: datetime


def should_hold_session(context: "ReplayContext", mutated_state: bool) -> bool:
    """The §6 table: hold for an operator present now, or when the run left
    uncommitted mutable state behind. An unattended read-only flow closes so
    the caller gets its failure immediately rather than an agent hanging.
    """
    return context.attended or mutated_state


def raise_intervention(
    *,
    surface: "Surface",
    context: "ReplayContext",
    evidence: EvidenceWriter,
    capability_id: str,
    capability_version: str,
    step_id: str,
    reason: EscalationReason,
    mutated_state: bool,
    sensitive_bounds: list[Rect] | None,
) -> InterventionRequest:
    observation = surface.observe()
    screenshot = surface.capture_screenshot(mask=sensitive_bounds)
    screenshot_path = evidence.save_screenshot(f"intervention_{step_id}", screenshot)

    session_held = should_hold_session(context, mutated_state)

    request = InterventionRequest(
        run_id=context.run_id,
        capability_id=capability_id,
        capability_version=capability_version,
        step_id=step_id,
        reason=reason,
        page=observation.page,
        screenshot_path=screenshot_path,
        session_held=session_held,
        raised_at=datetime.now(timezone.utc),
    )
    evidence.write_intervention(request)

    if not session_held:
        surface.close(keep_trace=context.keep_trace)

    return request
