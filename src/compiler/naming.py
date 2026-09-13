"""Capability ID derivation (docs/discovery-spec.md §6.5: "derived from the
goal, namespaced"). A small, mechanical heuristic, not NLP: pull a known
verb off the front of the goal, take the next word as the namespace, and
combine it with the primary discovered output (or a few leading words of
what's left) as the action name. Good enough to be reviewable and stable
for goals phrased the way this system's goals are phrased; a human names
the capability properly on review if the heuristic guesses badly -- the
artifact stays `draft` either way.
"""

from __future__ import annotations

import re

_VERB_PHRASES: list[tuple[str, str]] = [
    ("look up", "lookup"),
    ("search for", "lookup"),
    ("lookup", "lookup"),
    ("find", "lookup"),
    ("search", "lookup"),
    ("open", "open"),
    ("create", "create"),
    ("close", "close"),
    ("read", "read"),
    ("check", "check"),
    ("view", "view"),
    ("update", "update"),
    ("confirm", "confirm"),
]


def derive_capability_id(goal: str, primary_output: str | None) -> str:
    lowered = goal.strip().lower()

    verb = "run"
    rest = lowered
    for phrase, canonical in sorted(_VERB_PHRASES, key=lambda pair: -len(pair[0])):
        if lowered.startswith(phrase):
            verb = canonical
            rest = lowered[len(phrase) :].strip()
            break

    words = re.findall(r"[a-z0-9]+", rest)
    namespace = words[0] if words else "capability"

    tail = primary_output or "_".join(words[1:4]) or "action"
    action_name = tail if tail.startswith(verb) else f"{verb}_{tail}"

    return f"{namespace}.{action_name}"
