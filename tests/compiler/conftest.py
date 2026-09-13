from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agent.types import AgentAction, TraceStep
from src.surface.types import Observation, PageSignature, Rect, UINode

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


@pytest.fixture
def make_observation():
    def _make(
        nodes: list[UINode] | None = None,
        url: str = "http://127.0.0.1:5001/members/search",
        title: str = "Member Search",
        heading: str | None = None,
        app_version: str | None = "v4.2.1",
        state_hash: str = "hash0",
    ) -> Observation:
        return Observation(
            nodes=nodes if nodes is not None else [],
            page=PageSignature(url=url, title=title, heading=heading, app_version=app_version),
            screenshot=None,
            state_hash=state_hash,
            observed_at=datetime.now(timezone.utc),
        )

    return _make


@pytest.fixture
def make_trace_step(make_observation):
    def _make(
        index: int,
        action: AgentAction,
        *,
        node: UINode | None = None,
        before_hash: str = "h0",
        after_hash: str = "h1",
        before_kwargs: dict | None = None,
        after_kwargs: dict | None = None,
        risk: str = "safe",
    ) -> TraceStep:
        before = make_observation(state_hash=before_hash, **(before_kwargs or {}))
        after = make_observation(state_hash=after_hash, **(after_kwargs or {}))
        return TraceStep(index=index, agent_action=action, node=node, before=before, after=after, ok=True, risk=risk)

    return _make
