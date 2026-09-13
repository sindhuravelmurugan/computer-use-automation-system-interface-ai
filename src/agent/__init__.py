"""The discovery agent: the only part of the system where an LLM runs
(docs/discovery-spec.md). Never imported by src/replay/.
"""

from __future__ import annotations

from src.agent.factory import build_llm_client
from src.agent.llm import LLMClient, LLMProtocolError
from src.agent.loop import DiscoveryAgent
from src.agent.types import AgentAction, DiscoveryResult, HistoryEntry, StopReason, TraceStep

__all__ = [
    "build_llm_client",
    "LLMClient",
    "LLMProtocolError",
    "DiscoveryAgent",
    "AgentAction",
    "DiscoveryResult",
    "HistoryEntry",
    "StopReason",
    "TraceStep",
]
