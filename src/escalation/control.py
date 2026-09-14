"""The control-transfer model (docs/escalation-spec.md §1). Ownership of a
session is an explicit state with timeouts, not a boolean -- an intervention
can be raised and not yet picked up, and a human can take over and walk
away, so "who *should* be in control" has to be tracked separately from "who
*is*."

``SessionControl`` is persisted to ``evidence/{run_id}/control.json`` so it
survives the process boundary between the engine (or discovery agent) and
the operator Flask app: two different processes, one shared file each polls
and writes.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

DEFAULT_CLAIM_TIMEOUT_S = 300.0
DEFAULT_HOLD_TIMEOUT_S = 600.0

_TIMESTAMP_FIELDS = ("escalated_at", "claimed_at", "released_at", "reacquired_at")


class Controller(StrEnum):
    AUTOMATION = "automation"
    AWAITING_HUMAN = "awaiting_human"
    HUMAN = "human"
    RETURNING = "returning"
    ABANDONED = "abandoned"


class ControlTransitionError(RuntimeError):
    """Raised when a transition is attempted from a controller state that
    doesn't permit it (e.g. claiming a session nobody escalated).
    """


@dataclass
class SessionControl:
    run_id: str
    controller: Controller
    capability_id: str
    capability_version: str
    step_id: str
    reason: str

    cdp_endpoint: str | None = None
    page_url: str | None = None
    screenshot_path: str | None = None

    claimed_by: str | None = None
    escalated_at: datetime | None = None
    claimed_at: datetime | None = None
    released_at: datetime | None = None
    reacquired_at: datetime | None = None
    actions_at_claim: int = 0

    claim_timeout_s: float = DEFAULT_CLAIM_TIMEOUT_S
    hold_timeout_s: float = DEFAULT_HOLD_TIMEOUT_S

    handoffs: list[dict[str, Any]] = field(default_factory=list)

    # --- construction ------------------------------------------------------ #

    @classmethod
    def escalate(
        cls,
        *,
        run_id: str,
        capability_id: str,
        capability_version: str,
        step_id: str,
        reason: str,
        cdp_endpoint: str | None,
        page_url: str | None,
        screenshot_path: str | None,
        claim_timeout_s: float = DEFAULT_CLAIM_TIMEOUT_S,
        hold_timeout_s: float = DEFAULT_HOLD_TIMEOUT_S,
    ) -> "SessionControl":
        return cls(
            run_id=run_id,
            controller=Controller.AWAITING_HUMAN,
            capability_id=capability_id,
            capability_version=capability_version,
            step_id=step_id,
            reason=reason,
            cdp_endpoint=cdp_endpoint,
            page_url=page_url,
            screenshot_path=screenshot_path,
            escalated_at=datetime.now(timezone.utc),
            claim_timeout_s=claim_timeout_s,
            hold_timeout_s=hold_timeout_s,
        )

    # --- transitions (docs/escalation-spec.md §1 diagram) ------------------- #

    def claim(self, claimed_by: str, *, actions_at_claim: int = 0) -> None:
        """AWAITING_HUMAN -> HUMAN. Claiming is what transitions state --
        merely loading the operator page does not (§7). ``actions_at_claim``
        is the human_actions.jsonl line count at claim time -- a baseline so
        ``release_to_automation`` can report just *this* turn's action
        count on a run that escalates (and is claimed) more than once,
        rather than a running total.
        """
        if self.controller != Controller.AWAITING_HUMAN:
            raise ControlTransitionError(
                f"cannot claim from controller={self.controller!r}; expected awaiting_human"
            )
        self.controller = Controller.HUMAN
        self.claimed_by = claimed_by
        self.claimed_at = datetime.now(timezone.utc)
        self.actions_at_claim = actions_at_claim

    def release_to_automation(self, *, total_action_count: int = 0) -> None:
        """HUMAN -> RETURNING, signalling the engine (polling control.json)
        to reacquire. Records a handoff entry now -- who, when, how long,
        how many actions -- rather than waiting for the engine's
        verification outcome, since the handoff itself already happened
        regardless of what verification decides next. ``total_action_count``
        is the current (cumulative, whole-run) human_actions.jsonl line
        count; the handoff records only the delta since this claim.
        """
        if self.controller != Controller.HUMAN:
            raise ControlTransitionError(
                f"cannot release from controller={self.controller!r}; expected human"
            )
        self.controller = Controller.RETURNING
        self.released_at = datetime.now(timezone.utc)
        self._record_handoff(max(total_action_count - self.actions_at_claim, 0))

    def mark_reacquired(self) -> None:
        """RETURNING -> the engine has reconnected and is re-verifying. Not
        AUTOMATION yet -- that only happens once verification (docs/
        escalation-spec.md §5) actually passes.
        """
        self.reacquired_at = datetime.now(timezone.utc)

    def mark_verified(self) -> None:
        """Verification passed (or the human completed the flow outright) --
        control is genuinely back with automation.
        """
        self.controller = Controller.AUTOMATION

    def re_escalate(self, reason: str) -> None:
        """RETURNING -> AWAITING_HUMAN: verification failed, so this goes
        back for another human turn rather than blindly continuing (§5,
        "human leaves the app in an unrelated state").
        """
        self.controller = Controller.AWAITING_HUMAN
        self.reason = reason
        self.claimed_by = None
        self.claimed_at = None
        self.released_at = None
        self.reacquired_at = None
        self.escalated_at = datetime.now(timezone.utc)

    def abandon(self) -> None:
        """claim_timeout or hold_timeout -> ABANDONED. The run fails; the
        intervention (and, for a hold timeout, which handoff was in
        progress) stays on record in this same file.
        """
        self.controller = Controller.ABANDONED

    # --- timeouts (§1) ------------------------------------------------------ #

    def is_claim_timed_out(self, now: datetime | None = None) -> bool:
        if self.controller != Controller.AWAITING_HUMAN or self.escalated_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        return (now - self.escalated_at).total_seconds() > self.claim_timeout_s

    def is_hold_timed_out(self, last_activity_at: datetime, now: datetime | None = None) -> bool:
        if self.controller != Controller.HUMAN:
            return False
        now = now or datetime.now(timezone.utc)
        return (now - last_activity_at).total_seconds() > self.hold_timeout_s

    def _record_handoff(self, action_count: int) -> None:
        duration_s = None
        if self.claimed_at is not None and self.released_at is not None:
            duration_s = (self.released_at - self.claimed_at).total_seconds()
        self.handoffs.append(
            {
                "claimed_by": self.claimed_by,
                "escalated_at": _iso(self.escalated_at),
                "claimed_at": _iso(self.claimed_at),
                "released_at": _iso(self.released_at),
                "duration_s": duration_s,
                "action_count": action_count,
            }
        )

    # --- persistence (evidence/{run_id}/control.json) ----------------------- #

    @classmethod
    def path_for(cls, run_id: str, evidence_dir: str | Path = "evidence") -> Path:
        return Path(evidence_dir) / run_id / "control.json"

    def save(self, evidence_dir: str | Path = "evidence") -> None:
        path = self.path_for(self.run_id, evidence_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload["controller"] = self.controller.value
        for name in _TIMESTAMP_FIELDS:
            payload[name] = _iso(getattr(self, name))
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, run_id: str, evidence_dir: str | Path = "evidence") -> "SessionControl":
        path = cls.path_for(run_id, evidence_dir)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["controller"] = Controller(data["controller"])
        for name in _TIMESTAMP_FIELDS:
            if data.get(name):
                data[name] = datetime.fromisoformat(data[name])
        return cls(**data)

    @classmethod
    def exists(cls, run_id: str, evidence_dir: str | Path = "evidence") -> bool:
        return cls.path_for(run_id, evidence_dir).exists()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
