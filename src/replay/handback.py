"""Handback verification (docs/escalation-spec.md §5): on reacquire, decide
whether the human finished the whole capability, finished just the current
step, or left the app somewhere the engine doesn't recognize.

Deliberately does **not** search forward through later steps' checkpoints to
infer how far the human got. The failure mode of guessing wrong is
re-executing an irreversible step the human already performed by hand --
this target app's review-token guard happens to catch that specific case,
but a real back-office system might not, so refusing to infer is the
conservative default. The design this rules out for now: forward search
using ``human_actions.jsonl`` as evidence rather than inference.
"""

from __future__ import annotations

from typing import Any, Literal

from src.replay.detectors import evaluate_detector
from src.replay.templating import substitute_detector
from src.schema.artifact import CapabilityArtifact
from src.schema.steps import Step
from src.surface.types import Observation

HandbackVerdict = Literal["capability_success", "step_checkpoint_met", "unrecognized"]


def verify_handback(
    artifact: CapabilityArtifact,
    step: Step,
    resolved_inputs: dict[str, Any],
    observation: Observation,
) -> HandbackVerdict:
    """§5 steps 2-4 -- step 1 ("observe") is the caller's job; it already
    has a fresh ``Observation`` by the time this runs.
    """
    if evaluate_detector(artifact.success.checkpoint, observation):
        return "capability_success"

    if step.checkpoint is not None:
        checkpoint = substitute_detector(step.checkpoint, resolved_inputs)
        if evaluate_detector(checkpoint, observation):
            return "step_checkpoint_met"

    return "unrecognized"
