"""Pre-flight (docs/replay-spec.md §2): everything cheap that can reject a
run before a browser opens. In order: approval state, input validation,
override merge, allowlist, risk gate. Each failure is a distinct code; none
of them touches a Surface.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Callable

from src.replay.context import ReplayContext
from src.replay.overrides import OverrideError, merge_override
from src.schema.artifact import CapabilityArtifact
from src.schema.io import InputParam
from src.schema.overrides import TenantOverride
from src.schema.result import ErrorDetail
from src.surface.types import Action

if TYPE_CHECKING:
    from src.policy import PolicyGate

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


def check_allowlist(
    artifact: CapabilityArtifact, context: ReplayContext, policy_gate: "PolicyGate"
) -> ErrorDetail | None:
    """docs/policy-spec.md: the entry point goes through the same gate
    every other navigate action does -- not a separate flat prefix list.
    Cheap and real (docs/replay-spec.md §2.4): checked before a browser
    ever opens.
    """
    entry_point = artifact.surface.entry_point
    decision = policy_gate.check(Action(kind="navigate", url=entry_point), context)
    if decision.verdict != "allow":
        return ErrorDetail(
            code="ENTRY_POINT_NOT_ALLOWED",
            expected="entry_point allowed by the policy gate",
            observed=f"entry_point={entry_point!r} {decision.verdict} by rule {decision.rule!r}: {decision.reason}",
        )
    return None


def check_risk(artifact: CapabilityArtifact, context: ReplayContext) -> ErrorDetail | None:
    """Only the definitely-denied combination -- unattended and no
    allow_risky opt-in -- is rejected here. An attended run with a risky
    step is *not* rejected in pre-flight: docs/policy-spec.md's verdict
    table says attended -> requires_approval, which the engine surfaces by
    escalating when that step is actually reached, not by refusing the
    whole run before a human who is right there gets a chance to approve it.
    """
    risky_steps = [step.id for step in artifact.steps if step.risk == "risky"]
    if risky_steps and not context.allow_risky and not context.attended:
        return ErrorDetail(
            code="RISKY_STEPS_NOT_ALLOWED",
            expected="allow_risky=True, an attended session, or no risky steps in this artifact",
            observed=f"risky steps present: {risky_steps!r}, allow_risky=False, attended=False",
        )
    return None


def run_preflight(
    artifact: CapabilityArtifact,
    raw_inputs: dict[str, Any],
    context: ReplayContext,
    policy_gate: "PolicyGate",
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

    error = check_allowlist(merged_artifact, context, policy_gate)
    if error is not None:
        return None, None, error

    error = check_risk(merged_artifact, context)
    if error is not None:
        return None, None, error

    return merged_artifact, resolved_inputs, None
