from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.schema.overrides import TenantOverride


def _valid_override_dict() -> dict:
    return {
        "base_capability_id": "member.lookup_savings_balance",
        "base_version": "1.2.0",
        "tenant_id": "cu_riverbend",
        "surface": {"entry_point": "https://riverbend.example.test/member-search"},
        "step_overrides": {
            "step_002": {
                "target": {
                    "description": "Account number field",
                    "strategies": [
                        {"kind": "a11y", "role": "textbox", "name": "Account Number"}
                    ],
                }
            }
        },
        "outcome_overrides": {
            "MEMBER_NOT_FOUND": {
                "detect": {"kind": "text_matches", "pattern": "No matching account"}
            }
        },
    }


def test_valid_override_round_trips_through_json():
    override = TenantOverride.model_validate(_valid_override_dict())
    reloaded = TenantOverride.model_validate(json.loads(override.model_dump_json()))
    assert reloaded == override
    assert reloaded.step_overrides["step_002"].target.description == "Account number field"


def test_override_without_optional_sections_is_valid():
    payload = {
        "base_capability_id": "member.lookup_savings_balance",
        "base_version": "1.2.0",
        "tenant_id": "cu_riverbend",
    }
    override = TenantOverride.model_validate(payload)
    assert override.step_overrides == {}
    assert override.outcome_overrides == {}
    assert override.surface is None


def test_override_missing_base_capability_id_is_rejected():
    payload = _valid_override_dict()
    del payload["base_capability_id"]
    with pytest.raises(ValidationError):
        TenantOverride.model_validate(payload)


def test_override_rejects_unknown_detector_kind():
    payload = _valid_override_dict()
    payload["outcome_overrides"]["MEMBER_NOT_FOUND"]["detect"] = {
        "kind": "made_up_kind",
        "pattern": "x",
    }
    with pytest.raises(ValidationError):
        TenantOverride.model_validate(payload)


def test_override_does_not_support_adding_steps():
    """The doc is explicit: overrides patch by ID and cannot add steps.
    There is no `steps` field on the override at all — attempting to smuggle
    one in is simply ignored by the model, not accepted as new state."""
    payload = _valid_override_dict()
    payload["steps"] = [{"id": "step_999", "action": "click"}]
    override = TenantOverride.model_validate(payload)
    assert not hasattr(override, "steps")
