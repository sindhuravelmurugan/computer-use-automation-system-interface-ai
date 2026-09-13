"""Tenant override merge (docs/replay-spec.md §2.3, docs/capability-schema-v1.md
"Tenant override format"). Additive-only: an override can re-target existing
steps and adjust outcome detectors, never add or remove them. There is no
`steps` field on `TenantOverride` at all, so "can't add a step" is already
enforced by the schema; what this module still has to check is that every ID
an override *does* reference actually exists on the base artifact, and that
the override is meant for this artifact at all. Any of that failing is a
structural error -- a hard stop, never a silent hybrid.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.schema.artifact import CapabilityArtifact
from src.schema.overrides import TenantOverride


class OverrideError(ValueError):
    pass


def load_override_file(
    capability_id: str, tenant_id: str, base_dir: str | Path = "artifacts/overrides"
) -> TenantOverride | None:
    """Default override loader: artifacts/overrides/{capability_id}.{tenant_id}.json.
    Returns None when no such file exists -- a caller that names a tenant
    with no override on disk is a pre-flight error (§2.3), not silent
    fall-through to the base artifact.
    """
    path = Path(base_dir) / f"{capability_id}.{tenant_id}.json"
    if not path.exists():
        return None
    return TenantOverride.model_validate(json.loads(path.read_text(encoding="utf-8")))


def merge_override(artifact: CapabilityArtifact, override: TenantOverride) -> CapabilityArtifact:
    if override.base_capability_id != artifact.capability.id:
        raise OverrideError(
            f"override base_capability_id {override.base_capability_id!r} does not "
            f"match artifact id {artifact.capability.id!r}"
        )
    if override.base_version != artifact.capability.version:
        raise OverrideError(
            f"override base_version {override.base_version!r} does not match "
            f"artifact version {artifact.capability.version!r}"
        )

    merged = artifact.model_copy(deep=True)

    step_by_id = {step.id: step for step in merged.steps}
    for step_id, step_override in override.step_overrides.items():
        step = step_by_id.get(step_id)
        if step is None:
            raise OverrideError(f"override references unknown step_id {step_id!r}")
        if step_override.target is not None:
            step.target = step_override.target

    outcome_by_code = {outcome.code: outcome for outcome in merged.outcomes}
    for code, outcome_override in override.outcome_overrides.items():
        outcome = outcome_by_code.get(code)
        if outcome is None:
            raise OverrideError(f"override references unknown outcome code {code!r}")
        outcome.detect = outcome_override.detect

    if override.surface is not None:
        if override.surface.entry_point is not None:
            merged.surface.entry_point = override.surface.entry_point
        if override.surface.viewport is not None:
            merged.surface.viewport = override.surface.viewport
        if override.surface.requires_session is not None:
            merged.surface.requires_session = override.surface.requires_session

    merged.capability.tenant_id = override.tenant_id
    merged.capability.base_capability_id = override.base_capability_id
    return merged
