from __future__ import annotations

from src.replay.policy import AllowAllGate, AllowlistPolicyGate
from src.replay.context import ReplayContext
from src.surface.types import Action

ALLOWLIST = ["http://127.0.0.1:5001"]


def test_allow_all_gate_always_allows():
    gate = AllowAllGate()
    action = Action(kind="navigate", url="https://evil.example.com")
    decision = gate.check(action, ReplayContext(run_id="r1"))
    assert decision.allowed is True


def test_allowlist_gate_allows_navigate_within_allowlist():
    gate = AllowlistPolicyGate(ALLOWLIST)
    action = Action(kind="navigate", url="http://127.0.0.1:5001/members/search")
    decision = gate.check(action, ReplayContext(run_id="r1"))
    assert decision.allowed is True


def test_allowlist_gate_denies_navigate_outside_allowlist():
    gate = AllowlistPolicyGate(ALLOWLIST)
    action = Action(kind="navigate", url="https://evil.example.com/page")
    decision = gate.check(action, ReplayContext(run_id="r1"))
    assert decision.allowed is False
    assert "evil.example.com" in decision.reason


def test_allowlist_gate_allows_non_navigate_actions_unconditionally():
    gate = AllowlistPolicyGate(ALLOWLIST)
    action = Action(kind="click", target=None)
    decision = gate.check(action, ReplayContext(run_id="r1"))
    assert decision.allowed is True
