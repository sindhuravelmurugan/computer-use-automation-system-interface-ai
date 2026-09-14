from __future__ import annotations

from tests.policy.conftest import ctx, make_config

from src.policy.config import AllowlistConfig
from src.policy.gate import AllowAllGate, ConfiguredPolicyGate
from src.schema.common import A11yStrategy, SpatialStrategy, Target
from src.surface.types import Action


def nav(url: str, risk=None) -> Action:
    return Action(kind="navigate", url=url, risk=risk)


def test_allow_all_gate_always_allows_regardless_of_action_or_context():
    gate = AllowAllGate()
    decision = gate.check(nav("https://evil.example.com"), ctx(attended=False, allow_risky=False))
    assert decision.verdict == "allow"


def click(name: str | None, role: str = "button", risk=None) -> Action:
    target = Target(description="x", strategies=[A11yStrategy(role=role, name=name)]) if name else None
    return Action(kind="click", target=target, risk=risk)


# --- allowlist: domains --------------------------------------------------- #


def test_domain_outside_allowlist_is_denied(gate):
    decision = gate.check(nav("https://evil.example.com/members/search"), ctx())
    assert decision.verdict == "deny"
    assert decision.rule == "allowlist.domains"


def test_domain_in_allowlist_passes_domain_check(gate):
    decision = gate.check(nav("http://127.0.0.1:5001/members/search"), ctx())
    assert decision.verdict == "allow"


# --- allowlist: routes ----------------------------------------------------- #


def test_test_config_route_is_denied(gate):
    decision = gate.check(nav("http://127.0.0.1:5001/_test/config"), ctx())
    assert decision.verdict == "deny"
    assert decision.rule == "allowlist.routes"


def test_concrete_member_id_matches_param_pattern(gate):
    decision = gate.check(nav("http://127.0.0.1:5001/members/10001"), ctx())
    assert decision.verdict == "allow"


def test_route_with_wrong_segment_count_does_not_match(gate):
    decision = gate.check(nav("http://127.0.0.1:5001/members/10001/extra/segments"), ctx())
    assert decision.verdict == "deny"
    assert decision.rule == "allowlist.routes"


def test_subaccount_new_route_matches(gate):
    decision = gate.check(nav("http://127.0.0.1:5001/members/10007/subaccount/new"), ctx())
    assert decision.verdict == "allow"


# --- allowlist: action kinds ------------------------------------------------ #


def test_action_kind_not_allowlisted_is_denied():
    base = make_config()
    restricted_config = make_config(
        allowlist=AllowlistConfig(
            domains=base.allowlist.domains,
            routes=base.allowlist.routes,
            action_kinds=("navigate", "click", "type", "read", "wait_for", "assert"),  # no "select"
        )
    )
    gate = ConfiguredPolicyGate(restricted_config)
    decision = gate.check(Action(kind="select", target=None, value="x"), ctx())
    assert decision.verdict == "deny"
    assert decision.rule == "allowlist.action_kinds"


def test_allowlisted_action_kind_is_not_denied_by_that_rule(gate):
    decision = gate.check(click("Search"), ctx())
    assert decision.rule != "allowlist.action_kinds"


# --- non-navigate actions carry no route/domain check ----------------------- #


def test_click_action_is_not_domain_or_route_checked(gate):
    # clicks have no URL at all -- the gate can't and doesn't check
    # domain/route for them, only action_kind and risk.
    decision = gate.check(click("Search"), ctx())
    assert decision.verdict == "allow"


# --- risk: declared (replay) vs heuristic (discovery) ----------------------- #


def test_declared_safe_risk_is_authoritative_even_with_risky_looking_name(gate):
    decision = gate.check(click("Confirm and open", risk="safe"), ctx(attended=True))
    assert decision.verdict == "allow"


def test_declared_risky_risk_is_authoritative_even_with_harmless_name(gate):
    decision = gate.check(click("Search", risk="risky"), ctx(attended=True))
    assert decision.verdict == "requires_approval"


def test_heuristic_flags_risky_control_name(gate):
    decision = gate.check(click("Confirm and open"), ctx(attended=True))
    assert decision.verdict == "requires_approval"
    assert decision.rule == "risk.attended"


def test_heuristic_click_with_unreadable_name_is_risky(gate):
    # a target with only a spatial strategy has no readable accessible name
    target = Target(description="x", strategies=[SpatialStrategy(anchor_text="Savings", direction="right")])
    decision = gate.check(Action(kind="click", target=target), ctx(attended=True))
    assert decision.verdict == "requires_approval"


def test_heuristic_navigate_matching_risky_route_pattern(gate):
    decision = gate.check(nav("http://127.0.0.1:5001/members/10007/subaccount/confirm"), ctx(attended=True))
    assert decision.verdict == "requires_approval"


def test_non_click_action_with_harmless_name_is_safe(gate):
    target = Target(description="x", strategies=[A11yStrategy(role="textbox", name="Member ID")])
    decision = gate.check(Action(kind="type", target=target, value="10001"), ctx())
    assert decision.verdict == "allow"


# --- risk verdict table ------------------------------------------------------ #


def test_risky_allow_risky_true_allows(gate):
    decision = gate.check(click("Confirm and open"), ctx(allow_risky=True, attended=False))
    assert decision.verdict == "allow"
    assert decision.rule == "risk.allow_risky"


def test_risky_attended_requires_approval_not_silent_allow(gate):
    decision = gate.check(click("Confirm and open"), ctx(attended=True, allow_risky=False))
    assert decision.verdict == "requires_approval"
    assert decision.rule == "risk.attended"


def test_risky_unattended_denies(gate):
    decision = gate.check(click("Confirm and open"), ctx(attended=False, allow_risky=False))
    assert decision.verdict == "deny"
    assert decision.rule == "risk.unattended_risky"


def test_allow_risky_checked_before_attended(gate):
    # allow_risky=True wins even when also attended -- explicit caller
    # opt-in outranks "ask a human."
    decision = gate.check(click("Confirm and open"), ctx(allow_risky=True, attended=True))
    assert decision.verdict == "allow"


# --- every denial carries a rule --------------------------------------------- #


def test_every_non_allow_decision_carries_a_rule(gate):
    scenarios = [
        (nav("https://evil.example.com/x"), ctx()),
        (nav("http://127.0.0.1:5001/_test/config"), ctx()),
        (click("Confirm and open"), ctx(attended=False)),
        (click("Confirm and open"), ctx(attended=True)),
    ]
    for action, context in scenarios:
        decision = gate.check(action, context)
        assert decision.verdict != "allow"
        assert decision.rule is not None
