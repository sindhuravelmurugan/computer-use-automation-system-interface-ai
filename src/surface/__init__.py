"""The Surface seam. See docs/surface-spec.md.

Everything the agent loop and the replay engine need lives behind this
package's public names. Neither should ever ``import playwright`` — that
import is confined to ``src.surface.web``.
"""

from __future__ import annotations

from src.surface.protocol import Surface
from src.surface.serialize import serialize_observation
from src.surface.types import (
    Action,
    ActionResult,
    LocatorBundle,
    Observation,
    PageSignature,
    Rect,
    Resolution,
    SessionHandle,
    UINode,
    WaitSpec,
)

__all__ = [
    "Surface",
    "serialize_observation",
    "Action",
    "ActionResult",
    "LocatorBundle",
    "Observation",
    "PageSignature",
    "Rect",
    "Resolution",
    "SessionHandle",
    "UINode",
    "WaitSpec",
]
