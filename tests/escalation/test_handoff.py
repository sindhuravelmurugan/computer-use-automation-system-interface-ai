from __future__ import annotations

import json
from pathlib import Path

from src.escalation.control import Controller, SessionControl
from src.escalation.handoff import human_action_count, last_human_activity_at, wait_for_handoff


def make_control(run_id: str, **overrides) -> SessionControl:
    defaults = dict(
        run_id=run_id,
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


def test_wait_returns_immediately_when_already_returning(tmp_path):
    control = make_control("run-returning")
    control.claim("alice")
    control.release_to_automation(total_action_count=1)
    control.save(tmp_path)

    result = wait_for_handoff("run-returning", evidence_dir=tmp_path, poll_interval_s=0.01)

    assert result.outcome == "returned"
    assert result.control.controller == Controller.RETURNING


def test_wait_detects_claim_timeout_and_writes_abandoned(tmp_path):
    control = make_control("run-claim-timeout", claim_timeout_s=0.05)
    control.save(tmp_path)

    result = wait_for_handoff("run-claim-timeout", evidence_dir=tmp_path, poll_interval_s=0.02)

    assert result.outcome == "claim_timeout"
    assert result.control.controller == Controller.ABANDONED
    reloaded = SessionControl.load("run-claim-timeout", tmp_path)
    assert reloaded.controller == Controller.ABANDONED


def test_wait_detects_hold_timeout_with_no_activity(tmp_path):
    control = make_control("run-hold-timeout", hold_timeout_s=0.05)
    control.claim("alice")
    control.save(tmp_path)

    result = wait_for_handoff("run-hold-timeout", evidence_dir=tmp_path, poll_interval_s=0.02)

    assert result.outcome == "hold_timeout"
    assert result.control.controller == Controller.ABANDONED


def test_wait_extends_hold_timeout_when_actions_keep_arriving(tmp_path):
    """A human who is still actively clicking shouldn't time out just
    because they claimed a while ago -- the clock is against the last
    action, not the claim time.
    """
    control = make_control("run-active", hold_timeout_s=0.05)
    control.claim("alice")
    control.save(tmp_path)

    actions_path = Path(tmp_path) / "run-active" / "human_actions.jsonl"
    actions_path.parent.mkdir(parents=True, exist_ok=True)
    import time
    from datetime import datetime, timezone

    time.sleep(0.03)
    actions_path.write_text(
        json.dumps({"seq": 1, "at": datetime.now(timezone.utc).isoformat(), "kind": "click", "role": "button", "name": "Ack"})
        + "\n"
    )

    result = wait_for_handoff("run-active", evidence_dir=tmp_path, poll_interval_s=0.01)

    # The fresh action pushed the hold-timeout clock forward, so this
    # should time out later than 0.05s from claim, not right at it -- and
    # by the time it does, still correctly reports hold_timeout since
    # nothing ever released control.
    assert result.outcome == "hold_timeout"


def test_human_action_count_reads_jsonl(tmp_path):
    run_id = "run-count"
    path = Path(tmp_path) / run_id / "human_actions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"seq": 1}\n{"seq": 2}\n')

    assert human_action_count(run_id, tmp_path) == 2


def test_human_action_count_zero_when_no_file(tmp_path):
    assert human_action_count("nope", tmp_path) == 0


def test_last_human_activity_at_falls_back_when_no_file(tmp_path):
    from datetime import datetime, timezone

    fallback = datetime.now(timezone.utc)
    assert last_human_activity_at("nope", tmp_path, fallback) == fallback
