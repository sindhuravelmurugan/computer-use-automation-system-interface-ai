"""The policy gate (docs/policy-spec.md): allowlist, risk classification,
redaction -- one place for each of the three, per CLAUDE.md's layout.
"""

from __future__ import annotations

from src.policy.config import AllowlistConfig, PolicyConfig, RedactionConfig, RiskConfig
from src.policy.gate import AllowAllGate, ConfiguredPolicyGate, PolicyDecision, PolicyGate, Verdict
from src.policy.redaction import REDACTED, Redactor

__all__ = [
    "PolicyConfig",
    "AllowlistConfig",
    "RiskConfig",
    "RedactionConfig",
    "PolicyGate",
    "PolicyDecision",
    "Verdict",
    "AllowAllGate",
    "ConfiguredPolicyGate",
    "Redactor",
    "REDACTED",
]
