"""Waiting out a handoff (docs/escalation-spec.md §1, §7): the engine (or
discovery agent) that escalated blocks here, polling the same
``control.json`` the operator app writes to, until a human claims and
releases the session back -- or one of the two timeouts fires.

This module knows nothing about capability artifacts, replay, or discovery;
it only understands ``SessionControl`` and the human-actions log. The
capability-specific handback *verification* (docs/escalation-spec.md §5:
check the success condition, then the step checkpoint) is replay's own
concern -- see ``src.replay.handback`` -- because it needs the artifact and
the detector machinery, which must not become a dependency of this package.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from src.escalation.control import Controller, SessionControl

WaitOutcome = Literal["returned", "claim_timeout", "hold_timeout"]

DEFAULT_POLL_INTERVAL_S = 1.0


@dataclass
class WaitResult:
    outcome: WaitOutcome
    control: SessionControl


def human_action_count(run_id: str, evidence_dir: str | Path = "evidence") -> int:
    path = Path(evidence_dir) / run_id / "human_actions.jsonl"
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def last_human_activity_at(run_id: str, evidence_dir: str | Path, fallback: datetime) -> datetime:
    """The hold-timeout clock: the most recent recorded human action, or
    (nobody has acted since claiming yet) the claim time itself.
    """
    path = Path(evidence_dir) / run_id / "human_actions.jsonl"
    if not path.exists():
        return fallback
    last_at = fallback
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        at = datetime.fromisoformat(record["at"])
        if at > last_at:
            last_at = at
    return last_at


def wait_for_handoff(
    run_id: str,
    *,
    evidence_dir: str | Path = "evidence",
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    on_tick: Callable[[], None] | None = None,
) -> WaitResult:
    """Block until the human releases (RETURNING), or a timeout ends the
    wait. Both timeout paths write ABANDONED back to control.json themselves
    -- the diagram only names one state for "nobody's coming back," so a
    hold timeout reuses it, distinguished by the returned outcome and by
    `reason` staying whatever this escalation's original reason was.

    ``on_tick``, when given, is called once per poll -- the caller's
    ``Surface.pump_events()``, so a released session's captured human
    actions actually land on disk while this is still waiting (see that
    method's docstring), not only once the caller's next real Playwright
    call happens to flush them. Kept as a callback rather than a Surface
    parameter so this module stays surface-agnostic (see the module
    docstring).
    """
    while True:
        if on_tick is not None:
            on_tick()
        control = SessionControl.load(run_id, evidence_dir)
        now = datetime.now(timezone.utc)

        if control.controller == Controller.RETURNING:
            return WaitResult("returned", control)

        if control.controller == Controller.AWAITING_HUMAN and control.is_claim_timed_out(now):
            control.abandon()
            control.save(evidence_dir)
            return WaitResult("claim_timeout", control)

        if control.controller == Controller.HUMAN:
            assert control.claimed_at is not None
            last_activity = last_human_activity_at(run_id, evidence_dir, fallback=control.claimed_at)
            if control.is_hold_timed_out(last_activity, now):
                control.abandon()
                control.save(evidence_dir)
                return WaitResult("hold_timeout", control)

        if control.controller == Controller.ABANDONED:
            return WaitResult("claim_timeout", control)

        time.sleep(poll_interval_s)
