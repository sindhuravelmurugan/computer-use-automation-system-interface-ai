"""Policy configuration (docs/policy-spec.md §2). Loaded from
config/policy.json -- JSON for consistency with artifacts, one
serialization format in the repo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config/policy.json")


@dataclass(frozen=True)
class AllowlistConfig:
    domains: tuple[str, ...]
    routes: tuple[str, ...]
    action_kinds: tuple[str, ...]


@dataclass(frozen=True)
class RiskConfig:
    risky_route_patterns: tuple[str, ...]
    risky_control_names: tuple[str, ...]
    unattended_risky: str = "deny"


@dataclass(frozen=True)
class RedactionConfig:
    always_redact_keys: tuple[str, ...]
    patterns: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyConfig:
    allowlist: AllowlistConfig
    risk: RiskConfig
    redaction: RedactionConfig

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "PolicyConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "PolicyConfig":
        allow = data["allowlist"]
        risk = data["risk"]
        redaction = data["redaction"]
        return cls(
            allowlist=AllowlistConfig(
                domains=tuple(allow["domains"]),
                routes=tuple(allow["routes"]),
                action_kinds=tuple(allow["action_kinds"]),
            ),
            risk=RiskConfig(
                risky_route_patterns=tuple(risk["risky_route_patterns"]),
                risky_control_names=tuple(risk["risky_control_names"]),
                unattended_risky=risk.get("unattended_risky", "deny"),
            ),
            redaction=RedactionConfig(
                # Lowercased once here so every lookup site compares
                # case-insensitively without having to remember to.
                always_redact_keys=tuple(k.lower() for k in redaction["always_redact_keys"]),
                patterns=dict(redaction.get("patterns", {})),
            ),
        )
