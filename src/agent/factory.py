"""Provider selection (docs/discovery-spec.md §1): LLM_PROVIDER picks
Gemini (default) or Anthropic. This is the only place that decides which
concrete client to build -- the agent loop takes an `LLMClient` and never
branches on provider itself.
"""

from __future__ import annotations

import os

from src.agent.llm import LLMClient


def build_llm_client(provider: str | None = None) -> LLMClient:
    selected = (provider or os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()

    if selected == "gemini":
        from src.agent.gemini_client import GeminiClient

        return GeminiClient()
    if selected == "anthropic":
        from src.agent.anthropic_client import AnthropicClient

        return AnthropicClient()

    raise ValueError(f"unknown LLM_PROVIDER {selected!r}; expected 'gemini' or 'anthropic'")
