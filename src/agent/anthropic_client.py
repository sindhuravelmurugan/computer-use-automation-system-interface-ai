"""Anthropic implementation of LLMClient -- the drop-in alternative
docs/discovery-spec.md §1 calls for. Selected via LLM_PROVIDER=anthropic.
Same contract as GeminiClient: the agent loop never knows which one it's
talking to.
"""

from __future__ import annotations

import os

import anthropic

from src.agent.llm import LLMProtocolError
from src.agent.prompts import SYSTEM_PROMPT, render_prompt
from src.agent.tools import TOOLS, ToolCallError, agent_action_from_call
from src.agent.types import AgentAction, HistoryEntry

DEFAULT_MODEL = "claude-sonnet-5"


def _tools() -> list[dict]:
    return [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in TOOLS]


class AnthropicClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(api_key=key)
        self._model = model
        self._tools = _tools()

    def decide(self, goal: str, observation_text: str, history: list[HistoryEntry]) -> AgentAction:
        prompt = render_prompt(goal, observation_text, history)
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=self._tools,
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": prompt}],
        )

        for block in response.content:
            if block.type == "tool_use":
                try:
                    return agent_action_from_call(block.name, dict(block.input or {}))
                except ToolCallError as exc:
                    raise LLMProtocolError(str(exc)) from exc

        raise LLMProtocolError(f"model did not call a tool: {response.content!r}")
