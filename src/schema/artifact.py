"""The top-level capability artifact: the typed, versioned contract emitted
by a discovery run and consumed by the replay engine.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.schema.capability import CapabilityMeta, Provenance, SurfaceSpec
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


def _check_after_refs(check_after: list[str], step_id_set: set[str], owner: str) -> None:
    for ref in check_after:
        if ref != "any" and ref not in step_id_set:
            raise ValueError(f"{owner} check_after references unknown step_id {ref!r}")
