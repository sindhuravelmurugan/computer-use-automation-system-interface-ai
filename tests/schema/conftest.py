"""Shared fixtures: a valid capability artifact, built from the worked
example in docs/capability-schema-v1.md, plus a small helper for mutating a
deep copy of it in individual tests.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest


def _valid_artifact_dict() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "capability": {
            "id": "member.lookup_savings_balance",
            "version": "1.2.0",
            "name": "Look up member savings balance",
            "description": (
                "Searches for a member by ID and reads their current savings "
                "balance from the account summary screen."
            ),
            "approval_state": "approved",
            "base_capability_id": None,
            "tenant_id": None,
        },
        "surface": {
            "type": "web",
            "entry_point": "https://app.example-cu.test/members/search",
            "viewport": {"width": 1280, "height": 900},
            "requires_session": True,
        },
        "provenance": {
            "recorded_at": "2026-09-09T14:22:10Z",
            "recorded_by": "discovery-agent",
            "model": "claude-sonnet-4-6",
            "goal": "Look up member 10001 and read their savings balance",
            "run_id": "run_a3f9c2",
            "trace_ref": "evidence/run_a3f9c2/trace.jsonl",
            "human_edited": False,
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
                    "step_id": "step_005",
                    "target": {"$ref": "steps.step_005.target"},
                    "transform": "parse_currency",
                },
            }
        ],
        "steps": [
            {
                "id": "step_001",
                "action": "navigate",
                "value": "https://app.example-cu.test/members/search",
                "risk": "safe",
            },
            {
                "id": "step_002",
                "action": "type",
                "target": {
                    "description": "Member ID search field on the lookup screen",
                    "strategies": [
                        {"kind": "a11y", "role": "textbox", "name": "Member ID"},
                        {"kind": "label", "label_text": "Member ID", "control": "input"},
                        {
                            "kind": "spatial",
                            "anchor_text": "Member ID",
                            "direction": "right",
                        },
                        {"kind": "dom", "css": "#memberIdInput"},
                    ],
                },
                "value": "{{member_id}}",
                "risk": "safe",
                "checkpoint": {
                    "kind": "value_equals",
                    "target": {"$ref": "self.target"},
                    "expected": "{{member_id}}",
                },
                "wait": {"strategy": "settle", "timeout_ms": 5000},
                "on_timeout": "fail",
                "notes": (
                    "Field has no test ID; accessible name comes from the "
                    "adjacent label cell."
                ),
            },
            {
                "id": "step_003",
                "action": "click",
                "target": {
                    "description": "Search button",
                    "strategies": [
                        {"kind": "a11y", "role": "button", "name": "Search"},
                    ],
                },
                "risk": "safe",
            },
            {
                "id": "step_004",
                "action": "assert",
                "target": {
                    "description": "Account summary heading",
                    "strategies": [
                        {"kind": "a11y", "role": "heading", "name": "Account summary"},
                    ],
                },
                "risk": "safe",
                "checkpoint": {
                    "kind": "element_present",
                    "target": {"$ref": "self.target"},
                },
            },
            {
                "id": "step_005",
                "action": "read",
                "target": {
                    "description": "Savings balance value on the account summary screen",
                    "strategies": [
                        {"kind": "a11y", "role": "cell", "name": "Savings balance"},
                    ],
                },
                "risk": "safe",
            },
        ],
        "outcomes": [
            {
                "code": "MEMBER_NOT_FOUND",
                "kind": "business",
                "description": "No member exists with the supplied ID.",
                "detect": {
                    "any_of": [
                        {"kind": "text_matches", "pattern": "No member found"},
                        {
                            "kind": "element_present",
                            "target": {
                                "description": "No-results alert",
                                "strategies": [
                                    {
                                        "kind": "a11y",
                                        "role": "alert",
                                        "name": "Search returned no results",
                                    }
                                ],
                            },
                        },
                    ]
                },
                "check_after": ["step_003"],
                "returns": {"found": False},
                "terminal": True,
            }
        ],
        "recoveries": [
            {
                "code": "CONFIRMATION_INTERSTITIAL",
                "kind": "recoverable",
                "description": "Known 'acknowledge disclosure' modal that appears intermittently.",
                "detect": {
                    "kind": "element_present",
                    "target": {
                        "description": "Disclosure dialog",
                        "strategies": [
                            {"kind": "a11y", "role": "dialog", "name": "Disclosure"}
                        ],
                    },
                },
                "check_after": ["any"],
                "recovery": {
                    "action": "click",
                    "target": {
                        "description": "Acknowledge button",
                        "strategies": [
                            {"kind": "a11y", "role": "button", "name": "Acknowledge"}
                        ],
                    },
                },
                "max_attempts": 2,
                "then": "retry_step",
            },
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
            },
        ],
        "success": {
            "checkpoint": {
                "kind": "element_present",
                "target": {
                    "description": "Account summary heading",
                    "strategies": [
                        {"kind": "a11y", "role": "heading", "name": "Account summary"}
                    ],
                },
            },
            "require_all_outputs": True,
        },
    }


@pytest.fixture
def valid_artifact_dict() -> dict[str, Any]:
    return copy.deepcopy(_valid_artifact_dict())
