"""Pre-flight (docs/replay-spec.md §2): everything cheap that can reject a
run before a browser opens. In order: approval state, input validation,
override merge, allowlist, risk gate. Each failure is a distinct code; none
of them touches a Surface.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from src.replay.context import ReplayContext
from src.replay.overrides import OverrideError, merge_override
from src.schema.artifact import CapabilityArtifact
from src.schema.io import InputParam
from src.schema.overrides import TenantOverride
from src.schema.result import ErrorDetail

OverrideLoader = Callable[[str, str], "TenantOverride | None"]


def check_approval(artifact: CapabilityArtifact, context: ReplayContext) -> ErrorDetail | None:
    if artifact.capability.approval_state == "draft" and not context.attended:
        return ErrorDetail(
            code="DRAFT_UNATTENDED",
            expected="approval_state != 'draft', or an attended session",
            observed=f"approval_state='draft', attended={context.attended}",
        )
    return None


def _coerce_value(param: InputParam, value: Any) -> Any:
    if param.type == "string":
        if not isinstance(value, str):
            raise ValueError(f"expected a string, got {type(value).__name__}")
        pattern = (param.constraints or {}).get("pattern")
        if pattern is not None and re.fullmatch(pattern, value) is None:
            raise ValueError(f"does not match required pattern {pattern!r}")
        return value

    if param.type == "integer":
        if isinstance(value, bool):
            raise ValueError("expected an integer, got a boolean")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"expected an integer, got {value!r}") from exc

    if param.type in ("decimal", "money"):
        try:
            return Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"expected a decimal amount, got {value!r}") from exc

    if param.type == "date":
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError(f"expected an ISO date, got {value!r}") from exc

    if param.type == "boolean":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "false"):
            return str(value).lower() == "true"
        raise ValueError(f"expected a boolean, got {value!r}")

    if param.type == "enum":
        allowed = (param.constraints or {}).get("values")
        if allowed is not None and value not in allowed:
            raise ValueError(f"expected one of {allowed!r}, got {value!r}")
        return value

    raise ValueError(f"unknown input type {param.type!r}")


def validate_inputs(
    artifact: CapabilityArtifact, raw_inputs: dict[str, Any]
) -> tuple[dict[str, Any] | None, ErrorDetail | None]:
    resolved: dict[str, Any] = {}
    for param in artifact.inputs:
        if param.name not in raw_inputs or raw_inputs[param.name] is None:
            if param.required:
                return None, ErrorDetail(
                    code="INVALID_INPUT",
                    expected=f"input {param.name!r} is required",
                    observed="missing",
                )
            continue
        try:
            resolved[param.name] = _coerce_value(param, raw_inputs[param.name])
        except ValueError as exc:
            return None, ErrorDetail(
                code="INVALID_INPUT",
                expected=f"input {param.name!r}: {exc}",
                observed=repr(raw_inputs[param.name]),
            )
    return resolved, None


def apply_override(
    artifact: CapabilityArtifact,
    context: ReplayContext,
    override_loader: OverrideLoader,
) -> tuple[CapabilityArtifact | None, ErrorDetail | None]:
    if context.tenant_id is None:
        return artifact, None

    override = override_loader(artifact.capability.id, context.tenant_id)
    if override is None:
        return None, ErrorDetail(
            code="OVERRIDE_NOT_FOUND",
            expected=f"an override for tenant {context.tenant_id!r}",
            observed="no override file found",
        )
    try:
        merged = merge_override(artifact, override)
    except OverrideError as exc:
        return None, ErrorDetail(code="OVERRIDE_INVALID", expected="a structurally valid override", observed=str(exc))
    return merged, None


def check_allowlist(artifact: CapabilityArtifact, allowlist: list[str]) -> ErrorDetail | None:
    entry_point = artifact.surface.entry_point
    if not any(entry_point.startswith(prefix) for prefix in allowlist):
        return ErrorDetail(
            code="ENTRY_POINT_NOT_ALLOWED",
            expected=f"entry_point prefixed by one of {allowlist!r}",
            observed=entry_point,
        )
    return None


def check_risk(artifact: CapabilityArtifact, context: ReplayContext) -> ErrorDetail | None:
    risky_steps = [step.id for step in artifact.steps if step.risk == "risky"]
    if risky_steps and not context.allow_risky:
        return ErrorDetail(
            code="RISKY_STEPS_NOT_ALLOWED",
            expected="allow_risky=True, or no risky steps in this artifact",
            observed=f"risky steps present: {risky_steps!r}, allow_risky=False",
        )
    return None


def run_preflight(
    artifact: CapabilityArtifact,
    raw_inputs: dict[str, Any],
    context: ReplayContext,
    allowlist: list[str],
    override_loader: OverrideLoader,
) -> tuple[CapabilityArtifact | None, dict[str, Any] | None, ErrorDetail | None]:
    error = check_approval(artifact, context)
    if error is not None:
        return None, None, error

    resolved_inputs, error = validate_inputs(artifact, raw_inputs)
    if error is not None:
        return None, None, error

    merged_artifact, error = apply_override(artifact, context, override_loader)
    if error is not None:
        return None, None, error
    assert merged_artifact is not None

    error = check_allowlist(merged_artifact, allowlist)
    if error is not None:
        return None, None, error

    error = check_risk(merged_artifact, context)
    if error is not None:
        return None, None, error

    return merged_artifact, resolved_inputs, None
