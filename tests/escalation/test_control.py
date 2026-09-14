from __future__ import annotations

from datetime import timedelta

import pytest

from src.escalation.control import Controller, ControlTransitionError, SessionControl


def make_control(**overrides) -> SessionControl:
    defaults = dict(
        run_id="run1",
        capability_id="member.lookup_savings_balance",
        capability_version="1.0.0",
        step_id="step_002",
        reason="EXHAUSTED_RECOVERY",
        cdp_endpoint="http://127.0.0.1:9999",
        page_url="http://127.0.0.1:5001/members/10001",
        screenshot_path=None,
    )
    defaults.update(overrides)
    return SessionControl.escalate(**defaults)


def test_escalate_starts_awaiting_human():
    control = make_control()
    assert control.controller == Controller.AWAITING_HUMAN
    assert control.escalated_at is not None
    assert control.handoffs == []


def test_claim_transitions_to_human():
    control = make_control()
    control.claim("alice")
    assert control.controller == Controller.HUMAN
    assert control.claimed_by == "alice"
    assert control.claimed_at is not None


def test_claim_requires_awaiting_human():
    control = make_control()
    control.claim("alice")
    with pytest.raises(ControlTransitionError):
        control.claim("bob")


def test_loading_the_page_does_not_claim():
    """docs/escalation-spec.md §7: merely reading a SessionControl (what a
    GET to the operator page does) never mutates it -- only .claim() does.
    """
    control = make_control()
    _ = control.controller  # a "page load" would just read fields like this
    assert control.controller == Controller.AWAITING_HUMAN


def test_release_to_automation_transitions_and_records_handoff():
    control = make_control()
    control.claim("alice")
    control.release_to_automation(total_action_count=3)
    assert control.controller == Controller.RETURNING
    assert len(control.handoffs) == 1
    handoff = control.handoffs[0]
    assert handoff["claimed_by"] == "alice"
    assert handoff["action_count"] == 3
    assert handoff["duration_s"] is not None and handoff["duration_s"] >= 0


def test_release_requires_human():
    control = make_control()
    with pytest.raises(ControlTransitionError):
        control.release_to_automation(total_action_count=0)


def test_re_escalate_resets_claim_fields_but_keeps_prior_handoffs():
    control = make_control()
    control.claim("alice")
    control.release_to_automation(total_action_count=1)
    control.re_escalate("HANDBACK_UNVERIFIED")
    assert control.controller == Controller.AWAITING_HUMAN
    assert control.claimed_by is None
    assert control.claimed_at is None
    assert control.reason == "HANDBACK_UNVERIFIED"
    assert len(control.handoffs) == 1  # the completed handoff isn't lost


def test_abandon():
    control = make_control()
    control.abandon()
    assert control.controller == Controller.ABANDONED


def test_claim_timeout_detection():
    control = make_control(claim_timeout_s=1.0)
    assert control.is_claim_timed_out(control.escalated_at) is False
    assert control.is_claim_timed_out(control.escalated_at + timedelta(seconds=2)) is True


def test_claim_timeout_only_applies_while_awaiting_human():
    control = make_control(claim_timeout_s=0.0)
    control.claim("alice")
    assert control.is_claim_timed_out() is False


def test_hold_timeout_detection():
    control = make_control(hold_timeout_s=1.0)
    control.claim("alice")
    later = control.claimed_at + timedelta(seconds=2)
    assert control.is_hold_timed_out(control.claimed_at, now=control.claimed_at) is False
    assert control.is_hold_timed_out(control.claimed_at, now=later) is True


def test_hold_timeout_only_applies_while_human_controls():
    control = make_control(hold_timeout_s=0.0)
    # Still AWAITING_HUMAN -- nobody has claimed it yet, so there is no
    # "activity" clock running.
    assert control.is_hold_timed_out(control.escalated_at) is False


def test_save_and_load_roundtrip(tmp_path):
    control = make_control()
    control.claim("alice")
    control.release_to_automation(total_action_count=2)
    control.save(tmp_path)

    loaded = SessionControl.load("run1", tmp_path)
    assert loaded.controller == Controller.RETURNING
    assert loaded.claimed_by == "alice"
    assert loaded.escalated_at == control.escalated_at
    assert loaded.claimed_at == control.claimed_at
    assert loaded.handoffs == control.handoffs


def test_exists(tmp_path):
    assert SessionControl.exists("nope", tmp_path) is False
    make_control().save(tmp_path)
    assert SessionControl.exists("run1", tmp_path) is True
