"""A heuristic risk classification for discovered steps, used only to
decide what the compiler's prune pass must never discard (docs/discovery-
spec.md §6.1: "never prune an action whose risk is risky"). The model does
not declare risk -- there is no tool argument for it, and asking it to
self-assess would be exactly the kind of unverified guess §7 warns against
for outcomes. This is a narrow, explicitly heuristic stand-in until step 6's
real risk classification exists; it only has to be conservative enough that
an irreversible action never gets pruned as if it were a no-op loop.
"""

from __future__ import annotations

from typing import Literal

from src.agent.types import AgentAction
from src.surface.types import UINode

_RISKY_NAME_KEYWORDS = (
    "confirm",
    "submit",
    "save",
    "delete",
    "remove",
    "commit",
    "pay",
    "transfer",
    "approve",
    "open sub-account",
    "close account",
)


def classify_risk(action: AgentAction, node: UINode | None) -> Literal["safe", "risky"]:
    if action.kind not in ("click", "select"):
        return "safe"
    name = (node.name if node is not None else "") or ""
    lowered = name.lower()
    return "risky" if any(keyword in lowered for keyword in _RISKY_NAME_KEYWORDS) else "safe"
