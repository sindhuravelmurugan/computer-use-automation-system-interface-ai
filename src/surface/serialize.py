"""Serialize an Observation into the compact text block the discovery model
reads. Kept separate from observation building so prompt format can be tuned
without touching perception (docs/surface-spec.md §2).
"""

from __future__ import annotations

from src.surface.types import Observation, UINode

_VALUE_ROLES: frozenset[str] = frozenset({"textbox", "checkbox", "radio", "combobox"})


def _format_node(node: UINode) -> str:
    parts = [f"[{node.ref}] {node.role} \"{node.name}\""]

    if node.role in _VALUE_ROLES:
        shown = node.value if node.value else "empty"
        parts.append(f"({shown})")

    if not node.enabled:
        parts.append("(disabled)")

    if node.frame_path != ["main"]:
        parts.append(f"{{frame: {node.frame_path[-1]}}}")

    return " ".join(parts)


def serialize_observation(observation: Observation) -> str:
    return "\n".join(_format_node(node) for node in observation.nodes)
