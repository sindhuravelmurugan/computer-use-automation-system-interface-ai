from __future__ import annotations

from src.escalation.control import Controller, SessionControl
from src.escalation.operator_app import create_app


def make_control(tmp_path, run_id: str, **overrides) -> SessionControl:
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
    control = SessionControl.escalate(**defaults)
    control.save(tmp_path)
    return control


def client(tmp_path):
    app = create_app(str(tmp_path))
    app.testing = True
    return app.test_client()


def test_list_open_includes_awaiting_and_human_but_not_returning(tmp_path):
    make_control(tmp_path, "awaiting")
    human = make_control(tmp_path, "human-run")
    human.claim("alice")
    human.save(tmp_path)
    returning = make_control(tmp_path, "returning-run")
    returning.claim("bob")
    returning.release_to_automation(total_action_count=1)
    returning.save(tmp_path)

    resp = client(tmp_path).get("/operator")
    run_ids = {row["run_id"] for row in resp.get_json()}
    assert run_ids == {"awaiting", "human-run"}


def test_detail_includes_reason_step_and_screenshot_url(tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"fake-png")
    make_control(tmp_path, "run1", screenshot_path=str(shot))

    resp = client(tmp_path).get("/operator/run1")
    body = resp.get_json()
    assert body["capability_id"] == "member.lookup_savings_balance"
    assert body["step_id"] == "step_002"
    assert body["reason"] == "EXHAUSTED_RECOVERY"
    assert body["screenshot_url"] == "/operator/run1/screenshot"
    assert body["cdp_endpoint"] == "http://127.0.0.1:9999"


def test_claim_transitions_state(tmp_path):
    make_control(tmp_path, "run1")
    c = client(tmp_path)

    resp = c.post("/operator/run1/claim", json={"name": "alice"})
    assert resp.get_json()["controller"] == "human"
    assert SessionControl.load("run1", tmp_path).controller == Controller.HUMAN


def test_loading_detail_page_does_not_claim(tmp_path):
    make_control(tmp_path, "run1")
    c = client(tmp_path)
    c.get("/operator/run1")
    assert SessionControl.load("run1", tmp_path).controller == Controller.AWAITING_HUMAN


def test_claim_twice_conflicts(tmp_path):
    make_control(tmp_path, "run1")
    c = client(tmp_path)
    c.post("/operator/run1/claim", json={"name": "alice"})
    resp = c.post("/operator/run1/claim", json={"name": "bob"})
    assert resp.status_code == 409


def test_release_transitions_to_returning_and_records_action_count(tmp_path):
    make_control(tmp_path, "run1")
    c = client(tmp_path)
    c.post("/operator/run1/claim", json={"name": "alice"})

    # Actions recorded during the human's turn, i.e. after claiming --
    # the handoff's action_count is the delta since claim, not a running
    # total, so a run escalated more than once reports each turn correctly.
    actions_path = tmp_path / "run1" / "human_actions.jsonl"
    actions_path.write_text('{"seq":1}\n{"seq":2}\n')

    resp = c.post("/operator/run1/release")
    assert resp.get_json()["controller"] == "returning"
    control = SessionControl.load("run1", tmp_path)
    assert control.handoffs[0]["action_count"] == 2


def test_actions_endpoint_returns_captured_actions(tmp_path):
    make_control(tmp_path, "run1")
    actions_path = tmp_path / "run1" / "human_actions.jsonl"
    actions_path.write_text('{"seq":1,"kind":"click","role":"button","name":"Ack"}\n')

    resp = client(tmp_path).get("/operator/run1/actions")
    body = resp.get_json()
    assert body == [{"seq": 1, "kind": "click", "role": "button", "name": "Ack"}]


def test_unknown_run_id_is_404(tmp_path):
    resp = client(tmp_path).get("/operator/does-not-exist")
    assert resp.status_code == 404
