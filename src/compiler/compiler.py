"""Trace in, draft artifact out (docs/discovery-spec.md §6). This is where
the judgment lives: the raw model transcript stays in the trace file, and
what this module produces is a typed, versioned contract decoupled from it
-- never a serialization of the transcript itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.agent.types import DiscoveryResult, TraceStep
from src.compiler.bundles import build_locator_bundle
from src.compiler.canonicalize import canonicalize_artifact_routes
from src.compiler.checkpoints import infer_checkpoint
from src.compiler.naming import derive_capability_id
from src.compiler.prune import prune_trace
from src.schema.artifact import CapabilityArtifact
from src.schema.capability import CapabilityMeta, Provenance, SurfaceSpec
from src.schema.common import A11yStrategy, ElementPresentDetector, Target
from src.schema.io import InputParam, OutputParam, OutputSource, ParamType
from src.schema.steps import Step
from src.schema.success import SuccessCondition

DEFAULT_VERSION = "0.1.0"

# docs/discovery-spec.md §4: the transform is selected from the fixed
# registry based on the declared type. Only four transforms exist
# (src/replay/transforms.py); integer/boolean/enum have no dedicated
# transform, so they fall back to the closest safe option.
_TRANSFORM_BY_TYPE: dict[str, str] = {
    "money": "parse_currency",
    "decimal": "parse_currency",
    "date": "parse_date",
    "string": "trim",
    "integer": "trim",
    "boolean": "raw",
    "enum": "raw",
}

_REVIEW_NOTE = (
    "Kept despite being part of a repeated state -- contains a risky action; "
    "review whether this repetition is intentional."
)


@dataclass
class CompiledDraft:
    artifact: CapabilityArtifact
    review_notes: list[str] = field(default_factory=list)
    param_values: dict[str, str] = field(default_factory=dict)


def compile_trace(
    result: DiscoveryResult,
    *,
    model_name: str,
    recorded_by: str = "discovery-agent",
    capability_version: str = DEFAULT_VERSION,
) -> CompiledDraft:
    if result.final_observation is None or result.final_observation.page.heading is None:
        raise ValueError("cannot compile a run with no final observation / heading -- was it SUCCESS?")

    prune_result = prune_trace(result.trace_steps)

    steps: list[Step] = []
    inputs_by_name: dict[str, InputParam] = {}
    outputs: list[OutputParam] = []
    param_values: dict[str, str] = {}
    review_notes: list[str] = []

    # The agent's session bootstrap (src.agent.loop._bootstrap_login_if_needed)
    # runs *before* the model's decision loop, so a run that needed it never
    # records a navigate-to-entry-point action of its own -- the model's
    # first decision already starts from there. Left alone, a compiled
    # artifact like that has no target-free first step, so a fresh replay's
    # very first resolve() failure (wrong page, e.g. bounced to /login) is a
    # hard failure before any recovery ever gets a chance to run: resolve
    # failures short-circuit ahead of recovery checking (docs/replay-spec.md
    # §4.1). Every artifact needs that structural first step regardless of
    # what this one run happened to need.
    first_step_is_navigate_to_entry = (
        bool(prune_result.steps)
        and prune_result.steps[0].agent_action.kind == "navigate"
        and prune_result.steps[0].agent_action.url == result.entry_point
    )
    if not first_step_is_navigate_to_entry:
        synthetic_note = (
            "Synthesized by the compiler, not discovered: this run's session bootstrap "
            "(not a model decision) already reached the entry point before the model's "
            "first decision, so no navigate step survived pruning. Every artifact needs "
            "an explicit, target-free first step so a recovery has a chance to fire "
            "before any target-dependent step runs."
        )
        steps.append(Step(id="step_001", action="navigate", value=result.entry_point, risk="safe", notes=synthetic_note))
        review_notes.append(f"step_001: {synthetic_note}")

    for trace_step in prune_result.steps:
        step_id = f"step_{len(steps) + 1:03d}"
        action = trace_step.agent_action

        target, value = _target_and_value(trace_step, param_values, inputs_by_name)

        checkpoint, checkpoint_note = infer_checkpoint(trace_step)

        notes: list[str] = []
        if checkpoint_note:
            notes.append(checkpoint_note)
        if trace_step.index in prune_result.flagged_indices:
            notes.append(_REVIEW_NOTE)
            review_notes.append(f"{step_id}: {_REVIEW_NOTE}")

        steps.append(
            Step(
                id=step_id,
                action=action.kind,  # type: ignore[arg-type]  -- never "done"/"stuck" here
                target=target,
                value=value,
                risk=trace_step.risk,
                checkpoint=checkpoint,
                notes=" ".join(notes) if notes else None,
            )
        )

        if action.kind == "read" and action.output:
            output_type: ParamType = action.type if action.type in _TRANSFORM_BY_TYPE else "string"  # type: ignore[assignment]
            outputs.append(
                OutputParam(
                    name=action.output,
                    type=output_type,
                    required=True,
                    sensitive=action.sensitive,
                    description=f"Discovered from a 'read' step ({step_id}).",
                    source=OutputSource(
                        step_id=step_id,
                        target=target,
                        transform=_TRANSFORM_BY_TYPE.get(output_type, "raw"),
                    ),
                )
            )

    artifact = CapabilityArtifact(
        schema_version="1.0",
        capability=CapabilityMeta(
            id=derive_capability_id(result.goal, outputs[0].name if outputs else None),
            version=capability_version,
            name=result.goal[:1].upper() + result.goal[1:],
            description=f"Discovered from the goal: {result.goal}",
            approval_state="draft",  # never auto-approve
        ),
        surface=SurfaceSpec(type="web", entry_point=result.entry_point, requires_session=True),
        provenance=Provenance(
            recorded_at=datetime.now(timezone.utc),
            recorded_by=recorded_by,
            model=model_name,
            goal=result.goal,
            run_id=result.run_id,
            trace_ref=f"evidence/{result.run_id}/trace.jsonl",
            human_edited=False,
            recorded_against=result.app_version,
        ),
        inputs=list(inputs_by_name.values()),
        outputs=outputs,
        steps=steps,
        outcomes=[],  # the probe pass fills this in, if anything is discoverable
        recoveries=[],  # not discoverable from one happy-path run -- a review responsibility
        success=SuccessCondition(
            checkpoint=ElementPresentDetector(
                target=Target(
                    description=f'Heading "{result.final_observation.page.heading}"',
                    strategies=[A11yStrategy(role="heading", name=result.final_observation.page.heading)],
                )
            ),
            require_all_outputs=True,
        ),
    )

    canonicalize_artifact_routes(artifact, param_values)

    return CompiledDraft(artifact=artifact, review_notes=review_notes, param_values=param_values)


def _target_and_value(
    trace_step: TraceStep,
    param_values: dict[str, str],
    inputs_by_name: dict[str, InputParam],
) -> tuple[Target | None, str | None]:
    action = trace_step.agent_action

    if action.kind == "navigate":
        return None, action.url

    if action.kind == "wait_for":
        target = build_locator_bundle(trace_step.node) if trace_step.node is not None else None
        return target, None

    assert trace_step.node is not None
    target = build_locator_bundle(trace_step.node)

    if action.kind not in ("type", "select"):
        return target, None

    if not action.parameter:
        return target, action.value

    param_values[action.parameter] = action.value or ""
    if action.parameter not in inputs_by_name:
        inputs_by_name[action.parameter] = InputParam(
            name=action.parameter,
            type="string",
            required=True,
            sensitive=action.sensitive,
            description=(
                "Discovered input (value redacted)."
                if action.sensitive
                else f"Discovered from a {action.kind!r} step; observed value during discovery: {action.value!r}."
            ),
            example=None if action.sensitive else action.value,
        )
    return target, "{{" + action.parameter + "}}"
