from __future__ import annotations

from src.replay.handback import verify_handback
from src.schema.common import A11yStrategy, ElementPresentDetector, Target


def test_capability_success_checked_first(valid_artifact, make_observation, make_node):
    """docs/escalation-spec.md §5: the capability-level success condition is
    checked before the current step's own checkpoint -- if the human
    finished the whole flow, that's the answer regardless of which step was
    escalating.
    """
    obs = make_observation(nodes=[make_node(role="heading", name="Account summary")])
    step = valid_artifact.steps[1]
    assert verify_handback(valid_artifact, step, {}, obs) == "capability_success"


def test_step_checkpoint_met_when_capability_not_yet_done(valid_artifact, make_observation, make_node):
    checkpoint = ElementPresentDetector(
        target=Target(description="saved marker", strategies=[A11yStrategy(role="text", name="Saved")])
    )
    step = valid_artifact.steps[1].model_copy(update={"checkpoint": checkpoint})
    obs = make_observation(nodes=[make_node(role="text", name="Saved")])
    assert verify_handback(valid_artifact, step, {}, obs) == "step_checkpoint_met"


def test_unrecognized_when_neither_condition_holds(valid_artifact, make_observation):
    step = valid_artifact.steps[1]
    obs = make_observation(nodes=[])
    assert verify_handback(valid_artifact, step, {}, obs) == "unrecognized"


def test_unrecognized_when_step_has_no_checkpoint_and_capability_not_done(valid_artifact, make_observation):
    step = valid_artifact.steps[0]  # navigate step, no checkpoint declared
    assert step.checkpoint is None
    obs = make_observation(nodes=[])
    assert verify_handback(valid_artifact, step, {}, obs) == "unrecognized"
