"""The policy gate seam (docs/replay-spec.md §7). Step 6 owns the real
implementation — risk classification, redaction rules, per-tenant limits.
This module defines the interface now and ships a permissive default so the
engine has exactly one choke point from day one; retrofitting a gate call
into every action call site later would mean finding them all again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from src.replay.context import ReplayContext
    from src.surface.types import Action


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str | None = None


class PolicyGate(Protocol):
    def check(self, action: "Action", ctx: "ReplayContext") -> PolicyDecision: ...


class AllowAllGate:
    """Permissive default. Step 6 replaces this; the engine already calls
    gate.check() before every action, so that swap touches one place.
    """

    def check(self, action: "Action", ctx: "ReplayContext") -> PolicyDecision:
        return PolicyDecision(allowed=True)


class AllowlistPolicyGate:
    """The one slice of the real policy gate discovery needs now, ahead of
    step 6: `navigate` can target any URL the model picks, unlike replay's
    fixed `surface.entry_point`, so it needs the same allowlist check
    replay's pre-flight already does for its one fixed URL -- just applied
    per-action instead of once (docs/discovery-spec.md §5, §2 "same
    PolicyGate as replay"). Everything else stays permissive, exactly like
    AllowAllGate; risk classification and redaction rules are still step 6.
    """

    def __init__(self, allowlist: list[str]) -> None:
        self._allowlist = allowlist

    def check(self, action: "Action", ctx: "ReplayContext") -> PolicyDecision:
        if action.kind == "navigate" and action.url is not None:
            if not any(action.url.startswith(prefix) for prefix in self._allowlist):
                return PolicyDecision(
                    allowed=False, reason=f"url {action.url!r} is not in the allowlist {self._allowlist!r}"
                )
        return PolicyDecision(allowed=True)
