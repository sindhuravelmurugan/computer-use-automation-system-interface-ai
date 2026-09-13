"""Gemini implementation of LLMClient, via google-genai's function calling.
The default provider (docs/discovery-spec.md §1). Nothing here leaks into
the agent loop's own logic -- it only ever sees AgentAction.
"""

from __future__ import annotations

import os
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from src.agent.llm import LLMProtocolError
from src.agent.prompts import SYSTEM_PROMPT, render_prompt
from src.agent.tools import TOOLS, ToolCallError, agent_action_from_call
from src.agent.types import AgentAction, HistoryEntry

DEFAULT_MODEL = "gemini-flash-lite-latest"
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_S = 2.0


def _tool() -> types.Tool:
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
            for t in TOOLS
        ]
    )


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL) -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=key)
        self._model = model
        self._tool = _tool()

    def decide(self, goal: str, observation_text: str, history: list[HistoryEntry]) -> AgentAction:
        prompt = render_prompt(goal, observation_text, history)
        response = self._generate_with_retry(prompt)

        candidates = response.candidates or []
        if not candidates or not candidates[0].content or not candidates[0].content.parts:
            raise LLMProtocolError(f"model returned no content: {response!r}")

        for part in candidates[0].content.parts:
            if part.function_call is not None:
                try:
                    return agent_action_from_call(part.function_call.name, dict(part.function_call.args or {}))
                except ToolCallError as exc:
                    raise LLMProtocolError(str(exc)) from exc

        raise LLMProtocolError(f"model did not call a tool: {response.text!r}")

    def _generate_with_retry(self, prompt: str):
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[self._tool],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.ANY)
            ),
            temperature=0,
        )
        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return self._client.models.generate_content(model=self._model, contents=prompt, config=config)
            except genai_errors.ServerError as exc:
                # A transient 5xx from the provider, not a decision the
                # model made -- worth a couple of quick retries before
                # giving up, rather than crashing the whole discovery run
                # over a momentary overload.
                last_error = exc
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(_RETRY_BACKOFF_S * (attempt + 1))
        raise LLMProtocolError(f"Gemini API unavailable after {_MAX_ATTEMPTS} attempts: {last_error}") from last_error
