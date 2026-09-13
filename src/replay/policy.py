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
