from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from src.surface.types import (
    Action,
    ActionResult,
    Observation,
    PageSignature,
    Rect,
    Resolution,
    UINode,
)

ZERO_RECT = Rect(x=0, y=0, width=0, height=0)


class FakeSurface:
    """A minimal Surface double: one textbox, one page transition (typing
    into it "submits" and reveals a distinct result page). Enough to drive
    the agent loop through a real type -> done sequence without a browser.
    """

    surface_type = "fake"

    def __init__(self) -> None:
        self.opened_entry_point: str | None = None
        self._entry_point = ""
        self._typed_value: str | None = None

    def open(self, entry_point: str, run_id: str) -> None:
        self.opened_entry_point = entry_point
        self._entry_point = entry_point

    def close(self, keep_trace: bool) -> None:
        pass

    def observe(self) -> Observation:
        if self._typed_value is None:
            nodes = [
                UINode(
                    ref="n1", role="textbox", name="Field", value="", enabled=True, visible=True,
                    frame_path=["main"], bounds=ZERO_RECT, nearby_text=[], dom_hint="#f",
                )
            ]
            heading, url = None, self._entry_point
        else:
            nodes = [
                UINode(
                    ref="n1", role="textbox", name="Field", value=self._typed_value, enabled=True, visible=True,
                    frame_path=["main"], bounds=ZERO_RECT, nearby_text=[], dom_hint="#f",
                ),
                # Already on the page before the model ever mentions it --
                # this is what makes the "read" redaction case hard: the
                # value is visible in this very observation, before the
                # model's decision (which is what declares it sensitive)
                # even exists.
                UINode(
                    ref="n2", role="text", name="9,999.99", value=None, enabled=True, visible=True,
                    frame_path=["main"], bounds=ZERO_RECT, nearby_text=["Balance"], dom_hint="#bal",
                ),
            ]
            heading, url = "Result", self._entry_point + "/result"
        return Observation(
            nodes=nodes,
            page=PageSignature(url=url, title="t", heading=heading, app_version="v1.0.0"),
            screenshot=None,
            state_hash="H_RESULT" if self._typed_value is not None else "H_START",
            observed_at=datetime.now(timezone.utc),
        )

    def resolve(self, bundle) -> Resolution:
        obs = self.observe()
        for node in obs.nodes:
            for strategy in bundle.strategies:
                if getattr(strategy, "kind", None) == "a11y" and strategy.role == node.role and strategy.name == node.name:
                    return Resolution(node=node, strategy_index=0, candidates_found=1, status="resolved")
        return Resolution(node=None, strategy_index=None, candidates_found=0, status="not_found")

    def act(self, action: Action) -> ActionResult:
        start = time.monotonic()
        if action.kind == "type":
            self._typed_value = action.value
        obs_after = self.observe()
        return ActionResult(
            ok=True, resolution=None, error_code=None,
            duration_ms=int((time.monotonic() - start) * 1000), observation_after=obs_after,
        )

    def capture_screenshot(self, mask):
        return b""

    def release(self):
        raise NotImplementedError

    def reacquire(self, handle):
        raise NotImplementedError


class ScriptedLLMClient:
    """Returns a fixed, pre-scripted sequence of decisions -- one per call
    to decide(), in order. Raises if the script runs out.
    """

    def __init__(self, actions) -> None:
        self._actions = list(actions)

    def decide(self, goal, observation_text, history):
        if not self._actions:
            raise AssertionError("ScriptedLLMClient script exhausted")
        return self._actions.pop(0)


@pytest.fixture
def fake_surface():
    return FakeSurface()
