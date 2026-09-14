from __future__ import annotations

from tests.policy.conftest import make_config

from src.policy.gate import ConfiguredPolicyGate
from src.replay.context import ReplayContext
from src.replay.preflight import (
    check_allowlist,
    check_approval,
    check_risk,
    run_preflight,
    validate_inputs,
)

POLICY_GATE = ConfiguredPolicyGate(make_config())


def no_override_loader(capability_id, tenant_id):
    return None


# --- approval state ------------------------------------------------------- #


def test_draft_unattended_is_rejected(valid_artifact):
    valid_artifact.capability.approval_state = "draft"
    ctx = ReplayContext(run_id="r1", attended=False)
    err = check_approval(valid_artifact, ctx)
    assert err is not None
    assert err.code == "DRAFT_UNATTENDED"


def test_draft_attended_is_allowed(valid_artifact):
    valid_artifact.capability.approval_state = "draft"
    ctx = ReplayContext(run_id="r1", attended=True)
    assert check_approval(valid_artifact, ctx) is None


def test_approved_unattended_is_allowed(valid_artifact):
    ctx = ReplayContext(run_id="r1", attended=False)
    assert check_approval(valid_artifact, ctx) is None


# --- input validation ------------------------------------------------------ #


def test_valid_input_passes(valid_artifact):
    resolved, err = validate_inputs(valid_artifact, {"member_id": "10001"})
    assert err is None
    assert resolved == {"member_id": "10001"}


def test_missing_required_input_is_rejected(valid_artifact):
    resolved, err = validate_inputs(valid_artifact, {})
    assert resolved is None
    assert err.code == "INVALID_INPUT"


def test_input_violating_pattern_is_rejected(valid_artifact):
    resolved, err = validate_inputs(valid_artifact, {"member_id": "abc"})
    assert resolved is None
    assert err.code == "INVALID_INPUT"


def test_input_wrong_type_is_rejected(valid_artifact):
    resolved, err = validate_inputs(valid_artifact, {"member_id": 12345})
    assert resolved is None
    assert err.code == "INVALID_INPUT"


# --- allowlist -------------------------------------------------------------- #


def test_allowed_entry_point_passes(valid_artifact):
    ctx = ReplayContext(run_id="r1")
    assert check_allowlist(valid_artifact, ctx, POLICY_GATE) is None


def test_disallowed_entry_point_is_rejected(valid_artifact):
    valid_artifact.surface.entry_point = "https://evil.example.com/members/search"
    ctx = ReplayContext(run_id="r1")
    err = check_allowlist(valid_artifact, ctx, POLICY_GATE)
    assert err is not None
    assert err.code == "ENTRY_POINT_NOT_ALLOWED"


# --- risk gate ---------------------------------------------------------------- #


def test_safe_steps_never_blocked_by_risk_gate(valid_artifact):
    ctx = ReplayContext(run_id="r1", allow_risky=False)
    assert check_risk(valid_artifact, ctx) is None


def test_risky_step_without_allow_risky_is_rejected(valid_artifact):
    valid_artifact.steps[1].risk = "risky"
    ctx = ReplayContext(run_id="r1", allow_risky=False)
    err = check_risk(valid_artifact, ctx)
    assert err is not None
    assert err.code == "RISKY_STEPS_NOT_ALLOWED"


def test_risky_step_with_allow_risky_passes(valid_artifact):
    valid_artifact.steps[1].risk = "risky"
    ctx = ReplayContext(run_id="r1", allow_risky=True)
    assert check_risk(valid_artifact, ctx) is None


def test_risky_step_attended_is_not_rejected_in_preflight(valid_artifact):
    """docs/policy-spec.md's verdict table: attended -> requires_approval,
    not a pre-flight refusal. Only the definitely-denied combination
    (unattended, no allow_risky) short-circuits before a browser opens;
    an attended run proceeds and hits requires_approval live, when the
    risky step is actually reached.
    """
    valid_artifact.steps[1].risk = "risky"
    ctx = ReplayContext(run_id="r1", allow_risky=False, attended=True)
    assert check_risk(valid_artifact, ctx) is None


# --- full orchestration ------------------------------------------------------- #


def test_run_preflight_succeeds_end_to_end(valid_artifact):
    ctx = ReplayContext(run_id="r1")
    artifact, inputs, err = run_preflight(valid_artifact, {"member_id": "10001"}, ctx, POLICY_GATE, no_override_loader)
    assert err is None
    assert inputs == {"member_id": "10001"}
    assert artifact is not None


def test_run_preflight_stops_at_first_failure_without_touching_later_checks(valid_artifact):
    # draft + unattended should fail at approval, before input validation
    # ever runs -- bad input alone would also fail, but the code must be
    # the approval one.
    valid_artifact.capability.approval_state = "draft"
    ctx = ReplayContext(run_id="r1", attended=False)
    artifact, inputs, err = run_preflight(valid_artifact, {}, ctx, POLICY_GATE, no_override_loader)
    assert artifact is None
    assert inputs is None
    assert err.code == "DRAFT_UNATTENDED"


def test_run_preflight_tenant_override_not_found_is_rejected(valid_artifact):
    ctx = ReplayContext(run_id="r1", tenant_id="riverbend")
    artifact, inputs, err = run_preflight(valid_artifact, {"member_id": "10001"}, ctx, POLICY_GATE, no_override_loader)
    assert artifact is None
    assert err.code == "OVERRIDE_NOT_FOUND"
