"""Runtime types for the Surface seam.

These are execution-time values, not persisted schema — they exist for the
duration of a single agent or replay run and are never serialized into an
artifact. That is why they are plain dataclasses rather than the Pydantic
models used under ``src/schema/``.

``LocatorBundle`` is the one exception: it is not a new type. It is the
artifact-level ``Target`` (role/name/spatial/dom strategies, in stability
order) from ``src.schema.common``, re-used as-is. ``resolve()`` takes exactly
what an artifact step declares.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from src.schema.common import Target as LocatorBundle

__all__ = [
    "LocatorBundle",
    "Rect",
    "UINode",
    "PageSignature",
    "Observation",
    "Resolution",
    "WaitSpec",
    "Action",
    "ActionResult",
    "SessionHandle",
    "ActionKind",
    "ErrorCode",
]

ActionKind = Literal["navigate", "click", "type", "select", "read", "wait_for", "assert"]
ErrorCode = Literal["NOT_FOUND", "AMBIGUOUS", "TIMEOUT", "NOT_INTERACTABLE"]


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class UINode:
    ref: str
    role: str
    name: str
    value: str | None
    enabled: bool
    visible: bool
    frame_path: list[str]
    bounds: Rect
    nearby_text: list[str]
    dom_hint: str | None


@dataclass(frozen=True)
class PageSignature:
    url: str
    title: str
    heading: str | None
    app_version: str | None


@dataclass(frozen=True)
class Observation:
    nodes: list[UINode]
    page: PageSignature
    screenshot: bytes | None
    state_hash: str
    observed_at: datetime


@dataclass
class Resolution:
    node: UINode | None
    strategy_index: int | None
    candidates_found: int
    status: Literal["resolved", "not_found", "ambiguous"]


@dataclass(frozen=True)
class WaitSpec:
    strategy: Literal["load", "settle", "condition"] = "settle"
    timeout_ms: int = 10_000
    condition: LocatorBundle | None = None


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    target: LocatorBundle | None = None
    value: str | None = None
    url: str | None = None
    wait: WaitSpec = field(default_factory=WaitSpec)


@dataclass
class ActionResult:
    ok: bool
    resolution: Resolution | None
    error_code: ErrorCode | None
    duration_ms: int
    observation_after: Observation


@dataclass(frozen=True)
class SessionHandle:
    """Opaque handle for control transfer. See ``release``/``reacquire``."""

    cdp_endpoint: str
    browser_context_id: str
