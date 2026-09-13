from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

import pytest

from src.schema.artifact import CapabilityArtifact
from src.surface.types import Observation, PageSignature, Rect, UINode

ZERO_RECT = Rect(x=0, y=0, width=0, height=0)


@pytest.fixture
def make_node():
    def _make(
        ref: str = "n1",
        role: str = "text",
        name: str = "hello",
        value: str | None = None,
        enabled: bool = True,
        visible: bool = True,
        frame_path: list[str] | None = None,
        bounds: Rect = ZERO_RECT,
        nearby_text: list[str] | None = None,
        dom_hint: str | None = None,
    ) -> UINode:
        return UINode(
            ref=ref,
            role=role,
            name=name,
            value=value,
            enabled=enabled,
            visible=visible,
            frame_path=frame_path if frame_path is not None else ["main"],
            bounds=bounds,
            nearby_text=nearby_text if nearby_text is not None else [],
            dom_hint=dom_hint,
        )

    return _make


@pytest.fixture
def make_observation(make_node):
    def _make(
        nodes: list[UINode] | None = None,
        url: str = "http://127.0.0.1:5001/members/search",
        title: str = "Member Search",
        heading: str | None = None,
        app_version: str | None = "v4.2.1",
    ) -> Observation:
        return Observation(
            nodes=nodes if nodes is not None else [],
            page=PageSignature(url=url, title=title, heading=heading, app_version=app_version),
            screenshot=None,
            state_hash="deadbeef",
            observed_at=datetime.now(timezone.utc),
        )

    return _make


def _minimal_artifact_dict() -> dict[str, Any]:
    """A small but structurally complete artifact: navigate -> type -> click
    -> assert, one declared outcome, one declared recovery, one output.
    Enough to exercise pre-flight, override merge, and step-loop logic
    without dragging in the full worked example from tests/schema.
    """
    return {
        "schema_version": "1.0",
        "capability": {
            "id": "member.lookup_savings_balance",
            "version": "1.0.0",
            "name": "Look up member savings balance",
            "description": "Test fixture artifact.",
            "approval_state": "approved",
            "base_capability_id": None,
            "tenant_id": None,
        },
        "surface": {
            "type": "web",
            "entry_point": "http://127.0.0.1:5001/members/search",
            "requires_session": True,
        },
        "provenance": {
            "recorded_at": "2026-09-09T14:22:10Z",
            "recorded_by": "test-fixture",
            "model": "n/a",
            "goal": "test",
            "run_id": "run_fixture",
            "trace_ref": "evidence/run_fixture/trace.jsonl",
            "human_edited": False,
            "recorded_against": "v4.2.1",
        },
        "inputs": [
            {
                "name": "member_id",
                "type": "string",
                "required": True,
                "sensitive": False,
                "description": "Institution member identifier",
                "example": "10001",
                "constraints": {"pattern": "^[0-9]{5}$"},
            }
        ],
        "outputs": [
            {
                "name": "savings_balance",
                "type": "money",
                "required": True,
                "sensitive": True,
                "description": "Current available savings balance",
                "source": {
                    "step_id": "step_003",
                    "target": {"$ref": "steps.step_003.target"},
                    "transform": "parse_currency",
                },
            }
        ],
        "steps": [
            {
                "id": "step_001",
                "action": "navigate",
                "value": "http://127.0.0.1:5001/members/search",
                "risk": "safe",
            },
            {
                "id": "step_002",
                "action": "type",
                "target": {
                    "description": "Member ID field",
                    "strategies": [{"kind": "a11y", "role": "textbox", "name": "Member ID"}],
                },
                "value": "{{member_id}}",
                "risk": "safe",
            },
            {
                "id": "step_003",
                "action": "read",
                "target": {
                    "description": "Savings value",
                    "strategies": [{"kind": "a11y", "role": "text", "name": "4,832.10"}],
                },
                "risk": "safe",
            },
        ],
        "outcomes": [
            {
                "code": "MEMBER_NOT_FOUND",
                "kind": "business",
                "description": "No member exists with the supplied ID.",
                "detect": {"kind": "text_matches", "pattern": "No member found"},
                "check_after": ["step_002"],
                "returns": {"found": False},
                "terminal": True,
            }
        ],
        "recoveries": [
            {
                "code": "SESSION_EXPIRED",
                "kind": "recoverable",
                "description": "Session timed out and the app bounced to login.",
                "detect": {"kind": "url_matches", "pattern": ".*/login.*"},
                "check_after": ["any"],
                "recovery": {"action": "re_authenticate"},
                "max_attempts": 1,
                "then": "restart_from",
                "restart_step_id": "step_001",
            }
        ],
        "success": {
            "checkpoint": {
                "kind": "element_present",
                "target": {
                    "description": "Account summary heading",
                    "strategies": [{"kind": "a11y", "role": "heading", "name": "Account summary"}],
                },
            },
            "require_all_outputs": True,
        },
    }


@pytest.fixture
def artifact_dict() -> dict[str, Any]:
    return copy.deepcopy(_minimal_artifact_dict())


@pytest.fixture
def valid_artifact(artifact_dict: dict[str, Any]) -> CapabilityArtifact:
    return CapabilityArtifact.model_validate(artifact_dict)
