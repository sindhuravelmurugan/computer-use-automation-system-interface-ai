"""Escalation and handoff (docs/escalation-spec.md): who is in control of a
session, and the mechanism for ceding and reclaiming it.
"""

from __future__ import annotations

from src.escalation.control import Controller, ControlTransitionError, SessionControl
from src.escalation.handoff import (
    WaitOutcome,
    WaitResult,
    human_action_count,
    last_human_activity_at,
    wait_for_handoff,
)

__all__ = [
    "Controller",
    "ControlTransitionError",
    "SessionControl",
    "WaitOutcome",
    "WaitResult",
    "human_action_count",
    "last_human_activity_at",
    "wait_for_handoff",
]
