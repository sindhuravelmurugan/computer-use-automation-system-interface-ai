"""Declared business results — legitimate answers, not failures."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from src.schema.common import Detector


class Outcome(BaseModel):
    code: str
    kind: Literal["business"] = "business"
    description: str
    detect: Detector
    check_after: list[str]
    returns: dict[str, Any]
    terminal: bool = True
