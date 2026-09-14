"""The policy gate (docs/policy-spec.md). Replaces `AllowAllGate` as the
default everywhere: allowlist (deny by default) plus risk classification.
Redaction (§4) is a separate module applied at the evidence boundary, not
here -- this gate only ever decides whether an action proceeds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol
from urllib.parse import urlsplit

from src.policy.config import DEFAULT_CONFIG_PATH, PolicyConfig
from src.schema.common import A11yStrategy

if TYPE_CHECKING:
    from src.replay.context import ReplayContext
    from src.surface.types import Action, LocatorBundle

Verdict = Literal["allow", "deny", "requires_approval"]
RiskLevel = Literal["safe", "risky"]


@dataclass(frozen=True)
class PolicyDecision:
    verdict: Verdict
    rule: str | None = None
    reason: str | None = None


class PolicyGate(Protocol):
    def check(self, action: "Action", ctx: "ReplayContext") -> PolicyDecision: ...


class AllowAllGate:
    """Permissive stand-in. No longer the default anywhere in src/ as of
    docs/policy-spec.md -- kept for tests and for any seam that deliberately
    wants to bypass policy (there shouldn't be one outside tests).
    """

    def check(self, action: "Action", ctx: "ReplayContext") -> PolicyDecision:
        return PolicyDecision(verdict="allow")


def _route_matches(path: str, pattern: str) -> bool:
    """Routes are patterns with :param segments (docs/policy-spec.md §2
    "Route matching"), matched segment-by-segment against the canonicalized
    path -- the same shape the compiler already produces, so
    /members/10001 matches /members/:member_id without listing every
    member.
    """
    path_segments = [s for s in path.split("/") if s]
    pattern_segments = [s for s in pattern.split("/") if s]
    if len(path_segments) != len(pattern_segments):
        return False
    return all(pat.startswith(":") or pat == seg for seg, pat in zip(path_segments, pattern_segments))


def _control_name(target: "LocatorBundle | None") -> str | None:
    """The "control's accessible name" the risk rules talk about: the a11y
    strategy's name, when the bundle has one. A bundle with no a11y
    strategy at all (only spatial/dom) has no readable name -- distinct
    from an a11y strategy with an empty name, which the schema doesn't
    allow anyway.
    """
    if target is None:
        return None
    for strategy in target.strategies:
        if isinstance(strategy, A11yStrategy):
            return strategy.name
    return None


class ConfiguredPolicyGate:
    def __init__(self, config: PolicyConfig) -> None:
        self._config = config

    @classmethod
    def from_file(cls, path: str = str(DEFAULT_CONFIG_PATH)) -> "ConfiguredPolicyGate":
        return cls(PolicyConfig.load(path))

    def check(self, action: "Action", ctx: "ReplayContext") -> PolicyDecision:
        if action.kind not in self._config.allowlist.action_kinds:
            return PolicyDecision(
                verdict="deny",
                rule="allowlist.action_kinds",
                reason=f"action kind {action.kind!r} is not in the allowlisted action kinds",
            )

        # Only navigate carries a URL -- domain/route allowlisting is
        # necessarily scoped to it. The gate sees actions, not the page a
        # click/type/read/etc. happens to be acting on (docs/policy-spec.md
        # §5 "the gate sees actions, not consequences").
        if action.kind == "navigate" and action.url:
            domain_denial = self._check_domain(action.url)
            if domain_denial is not None:
                return domain_denial
            route_denial = self._check_route(action.url)
            if route_denial is not None:
                return route_denial

        risk = action.risk if action.risk is not None else self._classify_risk(action)
        if risk == "risky":
            return self._risk_verdict(ctx)

        return PolicyDecision(verdict="allow")

    def _check_domain(self, url: str) -> PolicyDecision | None:
        netloc = urlsplit(url).netloc
        if netloc not in self._config.allowlist.domains:
            return PolicyDecision(
                verdict="deny", rule="allowlist.domains", reason=f"domain {netloc!r} is not allowlisted"
            )
        return None

    def _check_route(self, url: str) -> PolicyDecision | None:
        path = urlsplit(url).path
        if not any(_route_matches(path, pattern) for pattern in self._config.allowlist.routes):
            return PolicyDecision(
                verdict="deny", rule="allowlist.routes", reason=f"route {path!r} is not allowlisted"
            )
        return None

    def _classify_risk(self, action: "Action") -> RiskLevel:
        """docs/policy-spec.md §3: no declared risk yet (discovery path),
        so classify conservatively from the action itself. Deliberately a
        heuristic, not semantic understanding -- see §5's stated limits.
        """
        if action.kind == "navigate" and action.url:
            path = urlsplit(action.url).path
            if any(re.search(pattern, path) for pattern in self._config.risk.risky_route_patterns):
                return "risky"

        name = _control_name(action.target)
        if name is not None and any(term in name.lower() for term in self._config.risk.risky_control_names):
            return "risky"

        if action.kind == "click" and name is None:
            return "risky"

        return "safe"

    def _risk_verdict(self, ctx: "ReplayContext") -> PolicyDecision:
        if ctx.allow_risky:
            return PolicyDecision(
                verdict="allow", rule="risk.allow_risky", reason="caller opted in via allow_risky=True"
            )
        if ctx.attended:
            return PolicyDecision(
                verdict="requires_approval", rule="risk.attended", reason="risky action requires operator approval"
            )
        verdict: Verdict = "deny" if self._config.risk.unattended_risky == "deny" else "allow"
        return PolicyDecision(
            verdict=verdict, rule="risk.unattended_risky", reason="risky action, unattended session"
        )
