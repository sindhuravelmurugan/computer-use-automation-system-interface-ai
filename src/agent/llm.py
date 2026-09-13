"""The LLM seam (docs/discovery-spec.md §1). Provider-agnostic because the
decision loop only needs constrained function calling -- nothing else about
the model is load-bearing, which is the point of compiling it out of the
replay path entirely.
"""

from __future__ import annotations

from typing import Protocol

from src.agent.types import AgentAction, HistoryEntry


class LLMProtocolError(RuntimeError):
    """The model responded without calling a tool, or called one with
    arguments that don't fit the declared vocabulary. Not a normal decision
    (like `stuck`) -- a client/provider-level failure to follow the
    contract.
    """


class LLMClient(Protocol):
    def decide(self, goal: str, observation_text: str, history: list[HistoryEntry]) -> AgentAction: ...
