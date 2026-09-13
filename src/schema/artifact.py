"""The top-level capability artifact: the typed, versioned contract emitted
by a discovery run and consumed by the replay engine.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.schema.capability import CapabilityMeta, Provenance, SurfaceSpec
from src.schema.common import (
    AllOfDetector,
    AnyOfDetector,
    Detector,
    ElementAbsentDetector,
    ElementPresentDetector,
    Target,
    TargetRef,
    ValueEqualsDetector,
)
from src.schema.io import InputParam, OutputParam
from src.schema.outcomes import Outcome
from src.schema.recoveries import Recovery
from src.schema.steps import Step
from src.schema.success import SuccessCondition

SchemaVersion = Literal["1.0"]


class CapabilityArtifact(BaseModel):
    schema_version: SchemaVersion
    capability: CapabilityMeta
    surface: SurfaceSpec
    provenance: Provenance
    inputs: list[InputParam] = Field(default_factory=list)
    outputs: list[OutputParam] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)
    outcomes: list[Outcome] = Field(default_factory=list)
    recoveries: list[Recovery] = Field(default_factory=list)
    success: SuccessCondition

    @model_validator(mode="after")
    def _check_cross_references(self) -> "CapabilityArtifact":
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step ids must be unique")
        step_id_set = set(step_ids)

        outcome_codes = [outcome.code for outcome in self.outcomes]
        if len(outcome_codes) != len(set(outcome_codes)):
            raise ValueError("outcome codes must be unique")

        recovery_codes = [recovery.code for recovery in self.recoveries]
        if len(recovery_codes) != len(set(recovery_codes)):
            raise ValueError("recovery codes must be unique")

        input_names = [param.name for param in self.inputs]
        if len(input_names) != len(set(input_names)):
            raise ValueError("input names must be unique")

        output_names = [param.name for param in self.outputs]
        if len(output_names) != len(set(output_names)):
            raise ValueError("output names must be unique")

        for output in self.outputs:
            if output.source.step_id not in step_id_set:
                raise ValueError(
                    f"output {output.name!r} references unknown step_id "
                    f"{output.source.step_id!r}"
                )

        for outcome in self.outcomes:
            _check_after_refs(outcome.check_after, step_id_set, f"outcome {outcome.code!r}")

        for recovery in self.recoveries:
            _check_after_refs(
                recovery.check_after, step_id_set, f"recovery {recovery.code!r}"
            )
            if (
                recovery.restart_step_id is not None
                and recovery.restart_step_id not in step_id_set
            ):
                raise ValueError(
                    f"recovery {recovery.code!r} restart_step_id "
                    f"{recovery.restart_step_id!r} is not a known step"
                )

        return self

    @model_validator(mode="after")
    def _resolve_target_refs(self) -> "CapabilityArtifact":
        """Resolve every $ref target eagerly, at artifact-load time.

        self.target and steps.step_N.target are both static pointers into this
        same document — nothing about them depends on runtime page state, so
        there is no reason to defer resolution to replay. After this runs,
        every TargetOrRef field on the artifact holds a concrete Target; the
        replay engine never sees a TargetRef.
        """
        steps_by_id = {step.id: step for step in self.steps}

        for step in self.steps:
            if step.checkpoint is not None:
                _resolve_detector_target_refs(
                    step.checkpoint, steps_by_id, step, f"step {step.id!r} checkpoint"
                )

        for outcome in self.outcomes:
            _resolve_detector_target_refs(
                outcome.detect, steps_by_id, None, f"outcome {outcome.code!r} detect"
            )

        for recovery in self.recoveries:
            _resolve_detector_target_refs(
                recovery.detect, steps_by_id, None, f"recovery {recovery.code!r} detect"
            )

        _resolve_detector_target_refs(
            self.success.checkpoint, steps_by_id, None, "success checkpoint"
        )

        for output in self.outputs:
            if isinstance(output.source.target, TargetRef):
                output.source.target = _resolve_ref(
                    output.source.target.ref,
                    steps_by_id,
                    None,
                    f"output {output.name!r} source",
                )

        return self


def _check_after_refs(check_after: list[str], step_id_set: set[str], owner: str) -> None:
    for ref in check_after:
        if ref != "any" and ref not in step_id_set:
            raise ValueError(f"{owner} check_after references unknown step_id {ref!r}")


_STEPS_REF_RE = re.compile(r"^steps\.([^.]+)\.target$")


def _resolve_ref(
    ref: str,
    steps_by_id: dict[str, Step],
    self_step: Step | None,
    owner: str,
) -> Target:
    """Resolve a single $ref string to the concrete Target it points at.

    Grammar: 'self.target' (only meaningful inside a step's own checkpoint,
    where it means "this step's target") or 'steps.<step_id>.target'.
    """
    if ref == "self.target":
        if self_step is None:
            raise ValueError(
                f"{owner}: $ref 'self.target' is only valid inside a step's own "
                "checkpoint"
            )
        if self_step.target is None:
            raise ValueError(
                f"{owner}: $ref 'self.target' but step {self_step.id!r} has no target"
            )
        return self_step.target

    match = _STEPS_REF_RE.match(ref)
    if match is None:
        raise ValueError(
            f"{owner}: malformed $ref {ref!r}; expected 'self.target' or "
            "'steps.<step_id>.target'"
        )

    step_id = match.group(1)
    step = steps_by_id.get(step_id)
    if step is None:
        raise ValueError(f"{owner}: $ref {ref!r} points to unknown step {step_id!r}")
    if step.target is None:
        raise ValueError(
            f"{owner}: $ref {ref!r} points to step {step_id!r}, which has no target"
        )
    return step.target


def _resolve_detector_target_refs(
    detector: Detector,
    steps_by_id: dict[str, Step],
    self_step: Step | None,
    owner: str,
) -> None:
    """Walk a (possibly composite) detector in place, resolving any TargetRef
    found on it to a concrete Target.
    """
    if isinstance(detector, (AnyOfDetector, AllOfDetector)):
        nested = detector.any_of if isinstance(detector, AnyOfDetector) else detector.all_of
        for sub_detector in nested:
            _resolve_detector_target_refs(sub_detector, steps_by_id, self_step, owner)
        return

    if isinstance(detector, (ElementPresentDetector, ElementAbsentDetector, ValueEqualsDetector)):
        if isinstance(detector.target, TargetRef):
            detector.target = _resolve_ref(detector.target.ref, steps_by_id, self_step, owner)
