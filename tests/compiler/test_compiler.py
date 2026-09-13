from __future__ import annotations

from src.agent.types import AgentAction, DiscoveryResult
from src.compiler.compiler import compile_trace


def _result(trace_steps, *, entry_point="http://fake/search", final_heading="Account summary"):
    return DiscoveryResult(
        run_id="run1",
        goal="Look up member 10001 and read their savings balance",
        stop_reason="SUCCESS",
        success=True,
        trace_steps=trace_steps,
        final_observation=trace_steps[-1].after if trace_steps else None,
        entry_point=entry_point,
        app_version="v4.2.1",
    )


def test_synthesizes_a_navigate_step_when_the_run_never_recorded_one(make_trace_step, make_node, make_observation):
    """Regression: a run whose session bootstrap absorbed the first
    navigation (so the model's first decision was already 'type') must
    still compile an artifact whose step_001 is a target-free navigate --
    otherwise a fresh replay's very first resolve() fails before any
    recovery ever gets a chance to run (resolve failures short-circuit
    ahead of recovery checking, docs/replay-spec.md §4.1)."""
    field = make_node(ref="n1", role="textbox", name="Field", dom_hint="#f")
    type_step = make_trace_step(
        0, AgentAction(kind="type", ref="n1", value="10001"), node=field,
        after_kwargs={"heading": "Account summary"},
    )
    result = _result([type_step], entry_point="http://fake/search")

    draft = compile_trace(result, model_name="test-model")

    assert draft.artifact.steps[0].action == "navigate"
    assert draft.artifact.steps[0].target is None
    assert draft.artifact.steps[0].value == "http://fake/search"
    assert draft.artifact.steps[0].notes is not None and "Synthesized" in draft.artifact.steps[0].notes
    assert draft.artifact.steps[1].action == "type"
    assert draft.artifact.steps[1].id == "step_002"
    assert any("step_001" in note for note in draft.review_notes)


def test_does_not_duplicate_navigate_when_the_run_already_recorded_one(make_trace_step, make_node):
    field = make_node(ref="n1", role="textbox", name="Field", dom_hint="#f")
    nav_step = make_trace_step(0, AgentAction(kind="navigate", url="http://fake/search"))
    type_step = make_trace_step(
        1, AgentAction(kind="type", ref="n1", value="10001"), node=field,
        after_kwargs={"heading": "Account summary"},
    )
    result = _result([nav_step, type_step], entry_point="http://fake/search")

    draft = compile_trace(result, model_name="test-model")

    assert draft.artifact.steps[0].action == "navigate"
    assert draft.artifact.steps[0].id == "step_001"
    assert draft.artifact.steps[1].action == "type"
    assert draft.artifact.steps[1].id == "step_002"
    assert len(draft.artifact.steps) == 2


def test_does_not_synthesize_when_recorded_navigate_targets_a_different_url(make_trace_step, make_node):
    """A navigate to somewhere other than the entry point (e.g. the model
    wandered) doesn't count -- the synthetic step is specifically about
    reaching the entry point, not about "any navigate happened"."""
    field = make_node(ref="n1", role="textbox", name="Field", dom_hint="#f")
    nav_step = make_trace_step(0, AgentAction(kind="navigate", url="http://fake/elsewhere"))
    type_step = make_trace_step(
        1, AgentAction(kind="type", ref="n1", value="10001"), node=field,
        after_kwargs={"heading": "Account summary"},
    )
    result = _result([nav_step, type_step], entry_point="http://fake/search")

    draft = compile_trace(result, model_name="test-model")

    assert draft.artifact.steps[0].value == "http://fake/search"
    assert draft.artifact.steps[0].notes is not None and "Synthesized" in draft.artifact.steps[0].notes
    assert draft.artifact.steps[1].value == "http://fake/elsewhere"
