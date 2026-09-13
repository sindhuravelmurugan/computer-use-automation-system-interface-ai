"""The seam. See docs/surface-spec.md.

The agent loop and the replay engine depend on this module (and on
``src.surface.types``) — never on ``playwright``, never on any concrete
implementation. Anything that needs adding to drive a new kind of UI goes
here first, as a protocol method every implementation must satisfy.
"""

from __future__ import annotations

from typing import Protocol

from src.surface.types import (
    Action,
    ActionResult,
    LocatorBundle,
    Observation,
    Rect,
    Resolution,
    SessionHandle,
)


class Surface(Protocol):
    surface_type: str

    def open(self, entry_point: str, run_id: str) -> None: ...

    def close(self, keep_trace: bool) -> None: ...

    def observe(self) -> Observation: ...

    def resolve(self, bundle: LocatorBundle) -> Resolution: ...

    def act(self, action: Action) -> ActionResult: ...

    def capture_screenshot(self, mask: list[Rect] | None) -> bytes: ...

    # Control transfer — used by escalation, step 7. Declared now because the
    # web implementation must launch with a remote debugging port from day
    # one; retrofitting that later means rewriting session setup.
    def release(self) -> SessionHandle: ...

    def reacquire(self, handle: SessionHandle) -> None: ...
