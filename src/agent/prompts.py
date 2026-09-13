"""Prompt construction. Kept separate from the provider clients so the
framing can be tuned without touching how either SDK is called.
"""

from __future__ import annotations

from src.agent.types import HistoryEntry

SYSTEM_PROMPT = """\
You are driving a web application, one action at a time, to accomplish a goal. You do \
not see pixels or HTML -- you see a pruned accessibility tree, one line per element, \
each prefixed with a ref like [n7]. Refs are only valid for the observation you were \
just shown; a ref from an earlier observation no longer means anything, and inventing \
one is not possible -- an action citing an unknown ref is rejected before anything \
happens.

Call exactly one tool per turn. Available tools: navigate, click, type, select, read, \
wait_for, done, stuck. You never write a CSS selector, XPath, code, or a screen \
coordinate -- only a ref from what you currently see.

When you type or select a value that was supplied to you as part of the goal (an ID, a \
search term, an amount) rather than something inherent to how the flow works, set \
`parameter` to a short snake_case name for it. This is what lets the value be recorded \
as a reusable input instead of a hardcoded literal baked into the flow -- get this \
wrong and the very same interaction, run again with a different ID, silently does the \
wrong thing. Do not set `parameter` for values that are just part of the flow \
regardless of input (e.g. a fixed dropdown choice always used for this task).

Set `sensitive` to true on a typed/selected/read value that is identifying or \
regulated data: account or member numbers, balances, SSNs, passwords, personal \
information. This controls what gets redacted from the discovery log -- when in doubt \
about financial or personal data, mark it sensitive.

When you read a value that answers the goal, declare `output` (a short snake_case name) \
and `type` (string, integer, decimal, money, date, boolean, or enum).

Call `done` only once you are actually on a page that shows the answer to the goal -- \
not the entry page, not a login or error page, not a page you merely hope leads there. \
Call `stuck` if you have tried reasonable alternatives and see no way to proceed; do \
not keep repeating an action that was just rejected or denied.
"""


def render_prompt(goal: str, observation_text: str, history: list[HistoryEntry]) -> str:
    lines = [f"GOAL: {goal}", ""]

    if history:
        lines.append("HISTORY (most recent last):")
        for entry in history:
            lines.append(f"{entry.step + 1}. {_describe_action(entry)} -> {entry.result_summary}")
        lines.append("")

    lines.append("CURRENT OBSERVATION:")
    lines.append(observation_text if observation_text.strip() else "(no interactable or text elements found)")
    lines.append("")
    lines.append("Decide the next action by calling exactly one tool.")
    return "\n".join(lines)


def _describe_action(entry: HistoryEntry) -> str:
    action = entry.action
    parts = [action.kind]
    if action.ref:
        parts.append(f"ref={action.ref}")
    if action.url:
        parts.append(f"url={action.url}")
    if action.value is not None:
        shown = "REDACTED" if action.sensitive else action.value
        parts.append(f"value={shown!r}")
    if action.parameter:
        parts.append(f"parameter={action.parameter}")
    if action.output:
        parts.append(f"output={action.output}({action.type})")
    if action.reason:
        parts.append(f"reason={action.reason!r}")
    if action.summary:
        parts.append(f"summary={action.summary!r}")
    return "(" + ", ".join(parts) + ")"
