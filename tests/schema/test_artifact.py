from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.schema.artifact import CapabilityArtifact
from src.schema.common import Target, TargetRef


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
        (
            lambda d: d["outputs"][0]["source"].__setitem__(
                "target", {"$ref": "steps.step_999.target"}
            ),
            "points to unknown step",
        ),
        (
            lambda d: d["outputs"][0]["source"].__setitem__(
                "target", {"$ref": "steps.step_005"}
            ),
            "malformed \\$ref",
        ),
        (
            lambda d: d["outputs"][0]["source"].__setitem__(
                "target", {"$ref": "step_005.target"}
            ),
            "malformed \\$ref",
        ),
        (
            lambda d: d["outcomes"][0]["detect"]["any_of"].append(
                {"kind": "element_present", "target": {"$ref": "self.target"}}
            ),
            "self.target' is only valid inside a step",
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


# --- $ref resolution ---------------------------------------------------------


def test_valid_ref_resolves_to_concrete_target_and_round_trips(valid_artifact_dict):
    artifact = CapabilityArtifact.model_validate(valid_artifact_dict)

    # steps.step_005.target, used by the savings_balance output, resolves to
    # step_005's own target and is a plain Target, never a TargetRef.
    output_target = artifact.outputs[0].source.target
    assert isinstance(output_target, Target)
    assert not isinstance(output_target, TargetRef)
    assert output_target == artifact.steps[4].target

    # self.target, used by step_002's checkpoint, resolves to step_002's own
    # target.
    checkpoint_target = artifact.steps[1].checkpoint.target
    assert isinstance(checkpoint_target, Target)
    assert checkpoint_target == artifact.steps[1].target

    dumped = json.loads(artifact.model_dump_json())
    # The $ref is gone from the serialized form — only the resolved inline
    # target remains.
    assert "$ref" not in json.dumps(dumped)

    reloaded = CapabilityArtifact.model_validate(dumped)
    assert reloaded == artifact
    assert isinstance(reloaded.outputs[0].source.target, Target)


def test_ref_to_nonexistent_step_is_rejected(valid_artifact_dict):
    valid_artifact_dict["outputs"][0]["source"]["target"] = {
        "$ref": "steps.step_999.target"
    }
    with pytest.raises(ValidationError, match="points to unknown step"):
        CapabilityArtifact.model_validate(valid_artifact_dict)


@pytest.mark.parametrize(
    "malformed_ref",
    [
        "steps.step_005",  # missing trailing .target
        "step_005.target",  # missing steps. prefix
        "steps..target",  # empty step id
        "not_a_ref",
        "self.wrong_field",
    ],
)
def test_ref_with_malformed_path_is_rejected(valid_artifact_dict, malformed_ref):
    valid_artifact_dict["outputs"][0]["source"]["target"] = {"$ref": malformed_ref}
    with pytest.raises(ValidationError, match="malformed \\$ref"):
        CapabilityArtifact.model_validate(valid_artifact_dict)


def test_self_ref_outside_step_checkpoint_is_rejected(valid_artifact_dict):
    valid_artifact_dict["success"]["checkpoint"] = {
        "kind": "element_present",
        "target": {"$ref": "self.target"},
    }
    with pytest.raises(ValidationError, match="only valid inside a step"):
        CapabilityArtifact.model_validate(valid_artifact_dict)


def test_ref_to_step_without_target_is_rejected(valid_artifact_dict):
    # step_001 is a navigate step with no target field.
    valid_artifact_dict["outputs"][0]["source"]["target"] = {
        "$ref": "steps.step_001.target"
    }
    with pytest.raises(ValidationError, match="has no target"):
        CapabilityArtifact.model_validate(valid_artifact_dict)
