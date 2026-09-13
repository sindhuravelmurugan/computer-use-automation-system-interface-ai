from __future__ import annotations

from src.agent.types import AgentAction
from src.compiler.prune import prune_trace


def test_empty_trace_returns_empty():
    result = prune_trace([])
    assert result.steps == []
    assert result.flagged_indices == set()


def test_single_type_step_with_unchanged_hash_survives(make_trace_step):
    """Regression: typing/reading legitimately don't change state_hash.
    A lone no-op action must not be treated as a self-canceling loop."""
    step = make_trace_step(0, AgentAction(kind="type", ref="n1", value="10001"), before_hash="S0", after_hash="S0")
    result = prune_trace([step])
    assert result.steps == [step]
    assert result.flagged_indices == set()


def test_type_then_click_then_read_all_survive(make_trace_step):
    """The exact shape of the real discovery run this bug was found in:
    type (S0->S0), click (S0->S1), read (S1->S1). Nothing here is a loop."""
    steps = [
        make_trace_step(0, AgentAction(kind="type", ref="n1", value="10001"), before_hash="S0", after_hash="S0"),
        make_trace_step(1, AgentAction(kind="click", ref="n2"), before_hash="S0", after_hash="S1"),
        make_trace_step(2, AgentAction(kind="read", ref="n3", output="balance", type="money"), before_hash="S1", after_hash="S1"),
    ]
    result = prune_trace(steps)
    assert result.steps == steps
    assert result.flagged_indices == set()


def test_genuine_backtrack_is_pruned(make_trace_step):
    """click into a sub-page (S0->S1), click back (S1->S0): both steps net
    to zero and should be dropped entirely."""
    steps = [
        make_trace_step(0, AgentAction(kind="click", ref="n1"), before_hash="S0", after_hash="S1"),
        make_trace_step(1, AgentAction(kind="click", ref="n2"), before_hash="S1", after_hash="S0"),
    ]
    result = prune_trace(steps)
    assert result.steps == []
    assert result.flagged_indices == set()


def test_backtrack_keeps_steps_before_the_loop(make_trace_step):
    steps = [
        make_trace_step(0, AgentAction(kind="navigate", url="https://x/a"), before_hash="S_start", after_hash="S0"),
        make_trace_step(1, AgentAction(kind="click", ref="n1"), before_hash="S0", after_hash="S1"),
        make_trace_step(2, AgentAction(kind="click", ref="n2"), before_hash="S1", after_hash="S0"),
    ]
    result = prune_trace(steps)
    assert result.steps == [steps[0]]


def test_risky_action_inside_a_loop_is_kept_and_flagged(make_trace_step):
    steps = [
        make_trace_step(0, AgentAction(kind="click", ref="n1"), before_hash="S0", after_hash="S1", risk="risky"),
        make_trace_step(1, AgentAction(kind="click", ref="n2"), before_hash="S1", after_hash="S0"),
    ]
    result = prune_trace(steps)
    assert result.steps == steps
    assert result.flagged_indices == {0, 1}


def test_non_risky_loop_after_a_risky_step_outside_it_is_still_pruned(make_trace_step):
    steps = [
        make_trace_step(0, AgentAction(kind="click", ref="n0"), before_hash="S_start", after_hash="S0", risk="risky"),
        make_trace_step(1, AgentAction(kind="click", ref="n1"), before_hash="S0", after_hash="S1"),
        make_trace_step(2, AgentAction(kind="click", ref="n2"), before_hash="S1", after_hash="S0"),
    ]
    result = prune_trace(steps)
    assert result.steps == [steps[0]]
    assert result.flagged_indices == set()
