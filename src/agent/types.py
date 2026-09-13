"""The model-facing action vocabulary (docs/discovery-spec.md §2) and the
shapes the agent loop passes around. The model may only ever produce an
`AgentAction` of one of these kinds -- it never writes a selector, code, or
a coordinate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.surface.types import Observation, UINode

AgentActionKind = Literal["navigate", "click", "type", "select", "read", "wait_for", "done", "stuck"]

StopReason = Literal["SUCCESS", "MAX_STEPS", "TIMEOUT", "NO_PROGRESS", "STUCK", "POLICY"]


@dataclass(frozen=True)
class AgentAction:
    kind: AgentActionKind
    ref: str | None = None
    url: str | None = None
    value: str | None = None
    parameter: str | None = None
    # Not in the spec's tool table verbatim -- a small, deliberate extension
    # of the same declare-it-yourself reasoning §3 gives for `parameter`:
    # the model knows why it typed or read something, so it is also the one
    # positioned to know whether that value is regulated/identifying data.
    # Declared per docs/discovery-spec.md's spirit, closes the gap left
    # unspecified for how trace redaction (§8, §10 "no sensitive parameter
    # value in trace.jsonl") knows what to redact.
    sensitive: bool = False
    output: str | None = None
    type: str | None = None
    reason: str | None = None
    summary: str | None = None


@dataclass(frozen=True)
class HistoryEntry:
    step: int
    action: AgentAction
    result_summary: str


@dataclass
class TraceStep:
    """One surviving (attempted-and-acted) step of the discovery run, with
    enough of the before/after picture for the compiler to build a locator
    bundle and infer a checkpoint without re-deriving anything from raw
    Playwright state.
    """

    index: int
    agent_action: AgentAction
    node: UINode | None
    before: Observation
    after: Observation
    ok: bool
    risk: Literal["safe", "risky"]


@dataclass
class DiscoveryResult:
    run_id: str
    goal: str
    stop_reason: StopReason
    success: bool
    trace_steps: list[TraceStep]
    final_observation: Observation | None
    entry_point: str
    app_version: str | None
    intervention_path: str | None = None
