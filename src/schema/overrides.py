"""Tenant override format. Not part of the artifact — merged over a base
artifact at load time. Overrides patch by ID and are additive: they can
re-target existing steps and adjust outcome detectors, never add or remove
steps.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.schema.common import Detector, Target
from src.schema.capability import ViewportSpec


class SurfaceOverride(BaseModel):
    entry_point: str | None = None
    viewport: ViewportSpec | None = None
    requires_session: bool | None = None


class StepOverride(BaseModel):
    target: Target | None = None


class OutcomeOverride(BaseModel):
    detect: Detector


class TenantOverride(BaseModel):
    base_capability_id: str
    base_version: str
    tenant_id: str
    surface: SurfaceOverride | None = None
    step_overrides: dict[str, StepOverride] = Field(default_factory=dict)
    outcome_overrides: dict[str, OutcomeOverride] = Field(default_factory=dict)
