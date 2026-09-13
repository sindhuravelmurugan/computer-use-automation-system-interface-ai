"""Pruning: a pure function from the raw node tree to what the model and the
replay engine are allowed to see.

Kept as its own module, with no Playwright import, so it can be unit tested
against hand-built fixtures. This is the component docs/surface-spec.md
flags as most likely to need tuning once the agent loop is running — that
tuning should never require touching the browser-driving code.
"""

from __future__ import annotations

from src.surface.types import UINode

INTERACTABLE_ROLES: frozenset[str] = frozenset(
    {"button", "link", "textbox", "checkbox", "radio", "combobox", "menuitem"}
)

DIALOG_ROLES: frozenset[str] = frozenset({"alert", "alertdialog", "dialog", "status"})

_KEPT_NON_DIALOG_ROLES: frozenset[str] = INTERACTABLE_ROLES | {"heading", "text"}


def _keep(node: UINode) -> bool:
    if node.role in DIALOG_ROLES:
        # Dialogs carry the failure/checkpoint signal even when the browser
        # currently reports them as not visible (e.g. mid-transition).
        return True

    if not node.visible:
        return False

    if node.role in INTERACTABLE_ROLES or node.role == "heading":
        return True

    if node.role == "text":
        return bool(node.name.strip())

    # Anything else is a presentational container or a role we don't
    # recognize as meaningful (e.g. decorative images) — drop it.
    return False


def prune_nodes(nodes: list[UINode]) -> list[UINode]:
    """Keep interactables, headings, dialog-family nodes, and non-empty text
    leaves. Drop everything else. See docs/surface-spec.md §2 "Pruning".
    """
    return [node for node in nodes if _keep(node)]
