from __future__ import annotations

import pytest

from src.surface.types import Rect, UINode

ZERO_RECT = Rect(x=0, y=0, width=0, height=0)


@pytest.fixture
def make_node():
    def _make(
        ref: str = "n1",
        role: str = "text",
        name: str = "hello",
        value: str | None = None,
        enabled: bool = True,
        visible: bool = True,
        frame_path: list[str] | None = None,
        bounds: Rect = ZERO_RECT,
        nearby_text: list[str] | None = None,
        dom_hint: str | None = None,
    ) -> UINode:
        return UINode(
            ref=ref,
            role=role,
            name=name,
            value=value,
            enabled=enabled,
            visible=visible,
            frame_path=frame_path if frame_path is not None else ["main"],
            bounds=bounds,
            nearby_text=nearby_text if nearby_text is not None else [],
            dom_hint=dom_hint,
        )

    return _make
