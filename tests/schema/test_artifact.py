from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.schema.artifact import CapabilityArtifact


def test_valid_artifact_round_trips_through_json(valid_artifact_dict):
    artifact = CapabilityArtifact.model_validate(valid_artifact_dict)

    dumped = json.loads(artifact.model_dump_json())
    reloaded = CapabilityArtifact.model_validate(dumped)

    assert reloaded == artifact
    assert reloaded.capability.id == "member.lookup_savings_balance"
    assert reloaded.steps[1].id == "step_002"
    assert reloaded.outputs[0].source.step_id == "step_005"


def test_step_target_strategies_preserve_order(valid_artifact_dict):
    artifact = CapabilityArtifact.model_validate(valid_artifact_dict)

    strategies = artifact.steps[1].target.strategies
    assert [s.kind for s in strategies] == ["a11y", "label", "spatial", "dom"]


def test_detector_composite_round_trips(valid_artifact_dict):
    artifact = CapabilityArtifact.model_validate(valid_artifact_dict)

    detect = artifact.outcomes[0].detect
    assert hasattr(detect, "any_of")
    assert len(detect.any_of) == 2


@pytest.mark.parametrize(
    "mutate, message_substring",
    [
        (
            lambda d: d["steps"].append({**d["steps"][0], "id": "step_001"}),
            "step ids must be unique",
        ),
        (
            lambda d: d.__setitem__(
                "outputs",
                [
                    {
                        **d["outputs"][0],
                        "source": {**d["outputs"][0]["source"], "step_id": "step_999"},
                    }
                ],
            ),
            "unknown step_id",
        ),
        (
            lambda d: d["outcomes"][0].__setitem__("check_after", ["step_999"]),
            "check_after references unknown step_id",
        ),
        (
            lambda d: d["recoveries"].append({**d["recoveries"][1], "code": "CONFIRMATION_INTERSTITIAL"}),
            "recovery codes must be unique",
        ),
        (
            lambda d: d["recoveries"][1].__setitem__("restart_step_id", "step_999"),
            "is not a known step",
        ),
    ],
)
def test_invalid_cross_references_are_rejected(valid_artifact_dict, mutate, message_substring):
    mutate(valid_artifact_dict)
    with pytest.raises(ValidationError, match=message_substring):
        CapabilityArtifact.model_validate(valid_artifact_dict)


def test_missing_required_field_is_rejected(valid_artifact_dict):
    del valid_artifact_dict["capability"]["id"]
    with pytest.raises(ValidationError):
        CapabilityArtifact.model_validate(valid_artifact_dict)


def test_bad_semver_is_rejected(valid_artifact_dict):
    valid_artifact_dict["capability"]["version"] = "not-a-version"
    with pytest.raises(ValidationError, match="semver"):
        CapabilityArtifact.model_validate(valid_artifact_dict)


def test_unknown_approval_state_is_rejected(valid_artifact_dict):
    valid_artifact_dict["capability"]["approval_state"] = "published"
    with pytest.raises(ValidationError):
        CapabilityArtifact.model_validate(valid_artifact_dict)


def test_empty_steps_is_rejected(valid_artifact_dict):
    valid_artifact_dict["steps"] = []
    with pytest.raises(ValidationError):
        CapabilityArtifact.model_validate(valid_artifact_dict)


def test_click_step_without_target_is_rejected(valid_artifact_dict):
    valid_artifact_dict["steps"][2].pop("target")
    with pytest.raises(ValidationError, match="requires a target"):
        CapabilityArtifact.model_validate(valid_artifact_dict)


def test_navigate_step_without_value_is_rejected(valid_artifact_dict):
    valid_artifact_dict["steps"][0].pop("value")
    with pytest.raises(ValidationError, match="requires a value"):
        CapabilityArtifact.model_validate(valid_artifact_dict)


def test_unknown_action_is_rejected(valid_artifact_dict):
    valid_artifact_dict["steps"][2]["action"] = "double_click"
    with pytest.raises(ValidationError):
        CapabilityArtifact.model_validate(valid_artifact_dict)


def test_dom_strategy_without_css_or_xpath_is_rejected(valid_artifact_dict):
    valid_artifact_dict["steps"][1]["target"]["strategies"][3] = {"kind": "dom"}
    with pytest.raises(ValidationError):
        CapabilityArtifact.model_validate(valid_artifact_dict)


def test_recovery_restart_from_without_restart_step_id_is_rejected(valid_artifact_dict):
    del valid_artifact_dict["recoveries"][1]["restart_step_id"]
    with pytest.raises(ValidationError, match="restart_from"):
        CapabilityArtifact.model_validate(valid_artifact_dict)
