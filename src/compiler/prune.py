"""The prune pass (docs/discovery-spec.md §6.1).

Three of the four prune rules are already enforced by the agent loop itself
-- it only ever appends a step to `trace_steps` once the action came back
`ok=True`, so a failed action, a rejected ref, or a policy denial never
reaches the compiler at all. What's left for this module is backtrack
detection: if the visible state (`state_hash`) returns to a value already
seen, the steps in between had no net effect and are dropped -- unless one
of them is risky, in which case the whole loop is kept and flagged for
review rather than discarded. Pruning something irreversible because the
screen looked the same afterwards is exactly the wrong instinct here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.agent.types import TraceStep


@dataclass
class PruneResult:
    steps: list[TraceStep]
    # trace_step.index values retained only because a risky action inside
    # a detected loop could not be safely dropped -- the compiler surfaces
    # these in the step's `notes` for review.
    flagged_indices: set[int] = field(default_factory=set)


def prune_trace(trace_steps: list[TraceStep]) -> PruneResult:
    if not trace_steps:
        return PruneResult(steps=[])

    kept: list[TraceStep] = []
    flagged: set[int] = set()
    # Maps a state_hash to the position in `kept` at which that hash was
    # the current state (i.e. the state before kept[position] executes).
    position_of_hash: dict[str, int] = {trace_steps[0].before.state_hash: 0}

    for step in trace_steps:
        kept.append(step)
        after_hash = step.after.state_hash

        loop_start = position_of_hash.get(after_hash)
        # A single step whose own before/after hash match (typing, a read,
        # an assert) is normal -- state_hash deliberately excludes field
        # values, so that action alone "returning" to its own starting
        # state isn't a backtrack to prune, just an action with no visible
        # side effect. Only a genuine multi-step excursion-and-return counts.
        if loop_start is not None and loop_start < len(kept) - 1:
            loop_slice = kept[loop_start:]
            if any(s.risk == "risky" for s in loop_slice):
                flagged.update(s.index for s in loop_slice)
                position_of_hash[after_hash] = len(kept)
            else:
                del kept[loop_start:]
                position_of_hash = {h: idx for h, idx in position_of_hash.items() if idx <= loop_start}
                position_of_hash[after_hash] = loop_start
        else:
            position_of_hash[after_hash] = len(kept)

    return PruneResult(steps=kept, flagged_indices=flagged)
