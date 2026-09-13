"""The tool vocabulary (docs/discovery-spec.md §2), described once as plain
JSON Schema and adapted by each provider client to its own function-calling
format. This is the whole reason the LLM seam is provider-agnostic: nothing
here is Gemini- or Anthropic-specific.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.agent.types import AgentAction

OUTPUT_TYPES = ["string", "integer", "decimal", "money", "date", "boolean", "enum"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="navigate",
        description="Go to a URL. Must be within the allowlist.",
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Absolute URL to navigate to."}},
            "required": ["url"],
        },
    ),
    ToolSpec(
        name="click",
        description="Click the element with this ref. The ref must come from the current observation.",
        parameters={
            "type": "object",
            "properties": {"ref": {"type": "string", "description": "A ref like 'n3' from the current observation."}},
            "required": ["ref"],
        },
    ),
    ToolSpec(
        name="type",
        description=(
            "Type a value into the textbox with this ref. If this value was supplied to you as part "
            "of the goal (an ID, a search term, an amount you were told to use) rather than something "
            "you chose yourself, set `parameter` to a short snake_case name for it -- this is what lets "
            "the compiled artifact take it as a typed input instead of a hardcoded literal. Do not set "
            "`parameter` for a value that is simply part of how this flow always works. Set `sensitive` "
            "to true if the value is identifying or regulated data (an account/member number, an SSN, "
            "a password) -- this controls what gets redacted from the discovery log."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "value": {"type": "string"},
                "parameter": {"type": "string", "description": "snake_case input name, only if this value was supplied to you."},
                "sensitive": {"type": "boolean", "description": "True if this value is identifying or regulated data."},
            },
            "required": ["ref", "value"],
        },
    ),
    ToolSpec(
        name="select",
        description="Choose an option in the dropdown/select with this ref. Same `parameter`/`sensitive` rules as `type`.",
        parameters={
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "value": {"type": "string", "description": "The visible option text to select."},
                "parameter": {"type": "string"},
                "sensitive": {"type": "boolean"},
            },
            "required": ["ref", "value"],
        },
    ),
    ToolSpec(
        name="read",
        description=(
            "Read a value from the element with this ref that answers the goal. Declare `output` (a "
            "short snake_case name for it) and `type` (one of: string, integer, decimal, money, date, "
            "boolean, enum). Set `sensitive` to true if the value is financial or otherwise regulated "
            "data -- a balance, a total, personal information."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "output": {"type": "string"},
                "type": {"type": "string", "enum": OUTPUT_TYPES},
                "sensitive": {"type": "boolean"},
            },
            "required": ["ref", "output", "type"],
        },
    ),
    ToolSpec(
        name="wait_for",
        description=(
            "Wait for the page to settle, or for a specific element (ref, from the current observation) "
            "to appear, before doing anything else."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["reason"],
        },
    ),
    ToolSpec(
        name="done",
        description="Call this once the goal has been fully accomplished -- you are on the page that answers it.",
        parameters={
            "type": "object",
            "properties": {"summary": {"type": "string", "description": "What was accomplished."}},
            "required": ["summary"],
        },
    ),
    ToolSpec(
        name="stuck",
        description="Call this if you cannot find a way to make progress toward the goal.",
        parameters={
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    ),
]

TOOLS_BY_NAME: dict[str, ToolSpec] = {tool.name: tool for tool in TOOLS}

_REQUIRED_ARGS: dict[str, tuple[str, ...]] = {
    "navigate": ("url",),
    "click": ("ref",),
    "type": ("ref", "value"),
    "select": ("ref", "value"),
    "read": ("ref", "output", "type"),
    "wait_for": ("reason",),
    "done": ("summary",),
    "stuck": ("reason",),
}


def agent_action_from_call(name: str, args: dict[str, Any]) -> AgentAction:
    """Turn a provider's raw function call into an AgentAction. Raises
    ToolCallError (caught by the caller and surfaced as an LLMProtocolError)
    for a call that doesn't fit the declared vocabulary at all -- an
    unrecognized tool name or a missing required argument. This is
    distinct from an invalid *ref*, which is a normal, expected rejection
    the agent loop handles by telling the model and continuing.
    """
    if name not in TOOLS_BY_NAME:
        raise ToolCallError(f"model called unknown tool {name!r}")
    missing = [arg for arg in _REQUIRED_ARGS[name] if not args.get(arg)]
    if missing:
        raise ToolCallError(f"tool {name!r} call is missing required argument(s) {missing!r}: {args!r}")

    return AgentAction(
        kind=name,  # type: ignore[arg-type]
        ref=args.get("ref"),
        url=args.get("url"),
        value=args.get("value"),
        parameter=args.get("parameter"),
        sensitive=bool(args.get("sensitive", False)),
        output=args.get("output"),
        type=args.get("type"),
        reason=args.get("reason"),
        summary=args.get("summary"),
    )


class ToolCallError(ValueError):
    pass
