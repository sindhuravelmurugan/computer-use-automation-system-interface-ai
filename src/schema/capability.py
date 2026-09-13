"""Identity/versioning, the perception seam, and provenance."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

ApprovalState = Literal["draft", "approved", "deprecated"]
SurfaceType = Literal["web", "legacy_web", "desktop"]


class CapabilityMeta(BaseModel):
    id: str
    version: str
    name: str
    description: str
    approval_state: ApprovalState
    base_capability_id: str | None = None
    tenant_id: str | None = None

    @field_validator("version")
    @classmethod
    def _validate_semver(cls, value: str) -> str:
        if not _SEMVER_RE.match(value):
            raise ValueError(f"version must be semver (x.y.z), got {value!r}")
        return value


class ViewportSpec(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class SurfaceSpec(BaseModel):
    type: SurfaceType
    entry_point: str
    viewport: ViewportSpec | None = None
    requires_session: bool = True


class Provenance(BaseModel):
    recorded_at: datetime
    recorded_by: str
    model: str
    goal: str
    run_id: str
    trace_ref: str
    human_edited: bool = False
