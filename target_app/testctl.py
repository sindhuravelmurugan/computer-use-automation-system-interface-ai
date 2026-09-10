"""Test control surface.

Not part of the simulated product. Namespaced under ``/_test/`` so the policy
allowlist can exclude it outright — the agent must never be able to reach it.

Flags are process-global rather than cookie-scoped on purpose: the test harness
that arms a failure is a different HTTP client from the browser the automation
drives, and a failure you can only arm from inside the session under test is not
a failure you can reproduce.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from flask import Blueprint, jsonify, request

from .data import reset_data


@dataclass
class Flags:
    """Injectable failure flags. Every one is deterministic once set."""

    expire_session: bool = False
    modal_on_next: int = 0
    latency_ms: int = 0
    error_on_next: bool = False

    def clear(self) -> None:
        self.expire_session = False
        self.modal_on_next = 0
        self.latency_ms = 0
        self.error_on_next = False


FLAGS = Flags()

# Bumped whenever a caller asks for session expiry. Sessions carry the epoch they
# were issued under; a mismatch means "expired", which is how a single flag can
# invalidate a session held by another HTTP client.
_session_epoch = 0


def session_epoch() -> int:
    return _session_epoch


def expire_all_sessions() -> None:
    global _session_epoch
    _session_epoch += 1


def consume_error_on_next() -> bool:
    if FLAGS.error_on_next:
        FLAGS.error_on_next = False
        return True
    return False


def consume_latency_ms() -> int:
    if FLAGS.latency_ms > 0:
        delay = FLAGS.latency_ms
        FLAGS.latency_ms = 0
        return delay
    return 0


def consume_modal() -> bool:
    if FLAGS.modal_on_next > 0:
        FLAGS.modal_on_next -= 1
        return True
    return False


bp = Blueprint("test_control", __name__, url_prefix="/_test")

_INT_FLAGS = {"modal_on_next", "latency_ms"}
_BOOL_FLAGS = {"expire_session", "error_on_next"}


@bp.get("/config")
def get_config():
    return jsonify(asdict(FLAGS))


@bp.post("/config")
def set_config():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "body must be a JSON object"}), 400

    unknown = set(payload) - _INT_FLAGS - _BOOL_FLAGS
    if unknown:
        return jsonify({"error": f"unknown flags: {sorted(unknown)}"}), 400

    for key in _INT_FLAGS:
        if key in payload:
            try:
                setattr(FLAGS, key, int(payload[key]))
            except (TypeError, ValueError):
                return jsonify({"error": f"{key} must be an integer"}), 400

    for key in _BOOL_FLAGS:
        if key in payload:
            setattr(FLAGS, key, bool(payload[key]))

    # Expiry is an event, not a state: apply it immediately, then clear, so the
    # reported flag state stays an honest description of what is still armed.
    if FLAGS.expire_session:
        expire_all_sessions()
        FLAGS.expire_session = False

    return jsonify(asdict(FLAGS))


@bp.post("/reset")
def reset():
    FLAGS.clear()
    reset_data()
    return jsonify({"reset": True, "flags": asdict(FLAGS)})
