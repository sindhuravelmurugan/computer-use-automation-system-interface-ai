"""Locator bundle construction (docs/discovery-spec.md §6.2): translate a
recorded `UINode` into a ranked `LocatorBundle`, including only the
strategies the node actually supports.

Two approximations, made because `UINode` (docs/surface-spec.md) doesn't
carry more than it does:

- **label**: `UINode` doesn't retain whether its accessible name came from
  a real `<label for>` versus anything else -- `WebSurface` already folds
  that into `.name` at observe time. For a form control (textbox/checkbox/
  radio/combobox) with a name, this emits a label strategy using that name;
  in this app real `<label for>` bindings mean a11y and label ranks
  genuinely agree, so this isn't a guess so much as a known-redundant rank.
- **spatial**: `UINode.nearby_text` is a flat list with no retained
  position, so a direction can't be derived exactly. This app's tables put
  the label to the left of the value it describes, so "right" from the
  first nearby_text entry is correct for the common case this system was
  built for.
"""

from __future__ import annotations

from src.schema.common import A11yStrategy, DomStrategy, LabelStrategy, SpatialStrategy, Target, TargetStrategy
from src.surface.types import UINode

_FORM_CONTROL_ROLES = frozenset({"textbox", "checkbox", "radio", "combobox"})


def build_locator_bundle(node: UINode, description: str | None = None) -> Target:
    strategies: list[TargetStrategy] = []

    if node.name:
        strategies.append(A11yStrategy(role=node.role, name=node.name))

    if node.role in _FORM_CONTROL_ROLES and node.name:
        strategies.append(LabelStrategy(label_text=node.name, control=node.role))

    if node.nearby_text:
        strategies.append(SpatialStrategy(anchor_text=node.nearby_text[0], direction="right"))

    if node.dom_hint:
        strategies.append(DomStrategy(css=node.dom_hint))

    if not strategies:
        raise ValueError(f"node {node.ref!r} ({node.role}, name={node.name!r}) supports no locator strategy")

    return Target(description=description or _default_description(node), strategies=strategies)


def _default_description(node: UINode) -> str:
    label = f'{node.role} "{node.name}"' if node.name else node.role
    return f"{label}, discovered via {node.dom_hint or 'accessibility tree'}"
