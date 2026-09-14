from __future__ import annotations

import pytest

from src.policy.config import AllowlistConfig, PolicyConfig, RedactionConfig, RiskConfig
from src.policy.gate import ConfiguredPolicyGate
from src.replay.context import ReplayContext


def make_config(**overrides) -> PolicyConfig:
    defaults = dict(
        allowlist=AllowlistConfig(
            domains=("127.0.0.1:5001", "127.0.0.1:5002"),
            routes=(
                "/login",
                "/members/search",
                "/members/search-frame",
                "/members/:member_id",
                "/members/:member_id/subaccount/new",
                "/members/:member_id/subaccount/review",
                "/members/:member_id/subaccount/confirm",
            ),
            action_kinds=("navigate", "click", "type", "select", "read", "wait_for", "assert"),
        ),
        risk=RiskConfig(
            risky_route_patterns=("/subaccount/confirm",),
            risky_control_names=("confirm", "submit", "delete", "transfer", "post", "commit"),
            unattended_risky="deny",
        ),
        redaction=RedactionConfig(
            always_redact_keys=("password", "token", "ssn", "tax_id", "card_number"),
            patterns={
                "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
                "card": r"\b(?:\d[ -]*?){13,16}\b",
            },
        ),
    )
    defaults.update(overrides)
    return PolicyConfig(**defaults)


@pytest.fixture
def config() -> PolicyConfig:
    return make_config()


@pytest.fixture
def gate(config) -> ConfiguredPolicyGate:
    return ConfiguredPolicyGate(config)


def ctx(**kwargs) -> ReplayContext:
    defaults = dict(run_id="test-run", attended=False, allow_risky=False)
    defaults.update(kwargs)
    return ReplayContext(**defaults)
