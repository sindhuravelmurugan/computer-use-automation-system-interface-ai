"""Declared business results — legitimate answers, not failures."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from src.schema.common import Detector

# docs/discovery-spec.md §7: auditability field so a reviewer can tell a
# detector grounded in an observed page ("probed") from one a human
# asserted ("declared") or signed off on after the fact ("reviewed").
OutcomeOrigin = Literal["probed", "declared", "reviewed"]


class Outcome(BaseModel):
    code: str
    kind: Literal["business"] = "business"
    description: str
    detect: Detector
    check_after: list[str]
    returns: dict[str, Any]
    terminal: bool = True
    origin: OutcomeOrigin = "declared"
