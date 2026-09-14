"""The operator surface (docs/escalation-spec.md §7): a minimal Flask app,
separate from the target app, on its own port. Explicitly mockable per the
brief -- no auth, no operator identity beyond a name field, no real-time
co-browsing, no queueing across multiple runs.

What is real: claiming is what transitions ``AWAITING_HUMAN`` -> ``HUMAN``
(merely loading a page does not), and the CDP URL handed back really does
open the live, already-running browser session -- not a fresh one.
"""

from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

from src.escalation.control import Controller, ControlTransitionError, SessionControl
from src.escalation.handoff import human_action_count

DEFAULT_PORT = 5055


def create_app(evidence_dir: str = "evidence") -> Flask:
    app = Flask(__name__)
    app.config["EVIDENCE_DIR"] = evidence_dir

    def _dir() -> str:
        return app.config["EVIDENCE_DIR"]

    def _load(run_id: str) -> SessionControl:
        if not SessionControl.exists(run_id, _dir()):
            abort(404, description=f"no control.json for run {run_id!r}")
        return SessionControl.load(run_id, _dir())

    @app.get("/operator")
    def list_open():
        base = Path(_dir())
        requests_open = []
        if base.exists():
            for run_dir in sorted(p for p in base.iterdir() if p.is_dir()):
                if not (run_dir / "control.json").exists():
                    continue
                control = SessionControl.load(run_dir.name, _dir())
                if control.controller in (Controller.AWAITING_HUMAN, Controller.HUMAN):
                    requests_open.append(_summary(control))
        return jsonify(requests_open)

    @app.get("/operator/<run_id>")
    def detail(run_id: str):
        control = _load(run_id)
        return jsonify(_detail(control))

    @app.post("/operator/<run_id>/claim")
    def claim(run_id: str):
        control = _load(run_id)
        body = request.get_json(silent=True) or {}
        name = body.get("name") or "operator"
        try:
            control.claim(name, actions_at_claim=human_action_count(run_id, _dir()))
        except ControlTransitionError as exc:
            abort(409, description=str(exc))
        control.save(_dir())
        return jsonify(_summary(control))

    @app.post("/operator/<run_id>/release")
    def release(run_id: str):
        control = _load(run_id)
        try:
            control.release_to_automation(total_action_count=human_action_count(run_id, _dir()))
        except ControlTransitionError as exc:
            abort(409, description=str(exc))
        control.save(_dir())
        return jsonify(_summary(control))

    @app.get("/operator/<run_id>/actions")
    def actions(run_id: str):
        path = Path(_dir()) / run_id / "human_actions.jsonl"
        if not path.exists():
            return jsonify([])
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return jsonify(records)

    @app.get("/operator/<run_id>/screenshot")
    def screenshot(run_id: str):
        control = _load(run_id)
        if not control.screenshot_path or not Path(control.screenshot_path).exists():
            abort(404, description="no screenshot on record for this request")
        return send_file(control.screenshot_path)

    return app


def _summary(control: SessionControl) -> dict:
    return {
        "run_id": control.run_id,
        "controller": control.controller.value,
        "capability_id": control.capability_id,
        "capability_version": control.capability_version,
        "step_id": control.step_id,
        "reason": control.reason,
        "claimed_by": control.claimed_by,
    }


def _detail(control: SessionControl) -> dict:
    summary = _summary(control)
    summary.update(
        {
            "page_url": control.page_url,
            "cdp_endpoint": control.cdp_endpoint,
            "screenshot_url": f"/operator/{control.run_id}/screenshot" if control.screenshot_path else None,
            "escalated_at": _iso(control.escalated_at),
            "claimed_at": _iso(control.claimed_at),
            "handoffs": control.handoffs,
        }
    )
    return summary


def _iso(value):
    return value.isoformat() if value is not None else None


if __name__ == "__main__":
    create_app().run(port=DEFAULT_PORT, debug=False)
