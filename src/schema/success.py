"""Capability-level completion. Reaching the last step is not success."""

from __future__ import annotations

from pydantic import BaseModel

from src.schema.common import Detector


class SuccessCondition(BaseModel):
    checkpoint: Detector
    require_all_outputs: bool = True
