"""Pydantic v2 models for the capability artifact schema (docs/capability-schema-v1.md).

Models only: no engine, no surface code. See CapabilityArtifact for the
top-level contract, ReplayResult for what the replay engine returns, and
TenantOverride for the multi-tenant patch format.
"""

from src.schema.artifact import CapabilityArtifact
from src.schema.capability import (
    ApprovalState,
    CapabilityMeta,
    Provenance,
    SurfaceSpec,
    SurfaceType,
    ViewportSpec,
)
from src.schema.common import (
    A11yStrategy,
    AllOfDetector,
    AnyOfDetector,
    Detector,
    DomStrategy,
    ElementAbsentDetector,
    ElementPresentDetector,
    LabelStrategy,
    SpatialStrategy,
    Target,
    TargetOrRef,
    TargetRef,
    TargetStrategy,
    TextMatchesDetector,
    UrlMatchesDetector,
    ValueEqualsDetector,
    WaitSpec,
)
from src.schema.io import InputParam, OutputParam, OutputSource, ParamType, Transform
from src.schema.outcomes import Outcome
from src.schema.overrides import (
    OutcomeOverride,
    StepOverride,
    SurfaceOverride,
    TenantOverride,
)
from src.schema.recoveries import Recovery, RecoveryAction, ThenAction
from src.schema.result import ErrorDetail, OutcomeResult, RecoveryApplied, ReplayResult, ReplayStatus
from src.schema.steps import ActionType, OnTimeout, RiskLevel, Step
from src.schema.success import SuccessCondition

__all__ = [
    "CapabilityArtifact",
    "ApprovalState",
    "CapabilityMeta",
    "Provenance",
    "SurfaceSpec",
    "SurfaceType",
    "ViewportSpec",
    "A11yStrategy",
    "AllOfDetector",
    "AnyOfDetector",
    "Detector",
    "DomStrategy",
    "ElementAbsentDetector",
    "ElementPresentDetector",
    "LabelStrategy",
    "SpatialStrategy",
    "Target",
    "TargetOrRef",
    "TargetRef",
    "TargetStrategy",
    "TextMatchesDetector",
    "UrlMatchesDetector",
    "ValueEqualsDetector",
    "WaitSpec",
    "InputParam",
    "OutputParam",
    "OutputSource",
    "ParamType",
    "Transform",
    "Outcome",
    "OutcomeOverride",
    "StepOverride",
    "SurfaceOverride",
    "TenantOverride",
    "Recovery",
    "RecoveryAction",
    "ThenAction",
    "ErrorDetail",
    "OutcomeResult",
    "RecoveryApplied",
    "ReplayResult",
    "ReplayStatus",
    "ActionType",
    "OnTimeout",
    "RiskLevel",
    "Step",
    "SuccessCondition",
]
