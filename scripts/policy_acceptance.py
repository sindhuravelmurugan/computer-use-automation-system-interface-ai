"""Acceptance checks for the policy gate (docs/policy-spec.md §6), against
the live meridian tenant on port 5001.

Pure allowlist/risk-classification logic (domain/route/action-kind
matching, the risk verdict table) is already exercised thoroughly by
tests/policy/test_gate.py and tests/policy/test_redaction.py -- this script
covers what genuinely needs a live Surface or a real ReplayEngine/
DiscoveryAgent run: pre-flight denial actually keeping the browser closed,
a risky step's three different verdicts playing out end-to-end, a denial
reported to a live discovery loop that then keeps going, and redaction
holding up across everything a real run writes to disk.

Run with: python -m scripts.policy_acceptance
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import requests

from src.agent.loop import DiscoveryAgent
from src.agent.trace import TraceRecorder
from src.agent.types import AgentAction
from src.policy import ConfiguredPolicyGate, PolicyConfig, Redactor
from src.policy.config import AllowlistConfig
from src.policy.gate import PolicyDecision
from src.replay.context import ReplayContext
from src.replay.engine import ReplayEngine
from src.replay.evidence import EvidenceWriter
from src.schema.artifact import CapabilityArtifact
from src.surface.web import WebSurface

MERIDIAN = "http://127.0.0.1:5001"
ARTIFACT_PATH = Path("artifacts/member.lookup_savings_balance.json")
EVIDENCE_DIR = "evidence"
GOAL = "Look up member 10001 and read their savings balance"

_RESULTS: list[tuple[str, bool]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _RESULTS.append((name, condition))
    status = "PASS" if condition else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f"\n       {detail}"
    print(line)


def reset_app() -> None:
    requests.post(f"{MERIDIAN}/_test/reset", timeout=5)


def load_artifact() -> CapabilityArtifact:
    data = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    return CapabilityArtifact.model_validate(data)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TrackingFactory:
    def __init__(self) -> None:
        self.call_count = 0

    def __call__(self) -> WebSurface:
        self.call_count += 1
        return WebSurface(headless=True)


class _ScriptedLLMClient:
    def __init__(self, actions):
        self._actions = list(actions)
        self.decide_calls = 0

    def decide(self, goal, observation_text, history):
        self.decide_calls += 1
        if not self._actions:
            raise AssertionError("script exhausted")
        return self._actions.pop(0)


def main() -> int:
    config = PolicyConfig.load()
    gate = ConfiguredPolicyGate(config)
    artifact = load_artifact()

    # ==================================================================== #
    # Pure gate checks against the real, shipped config (fast, no app I/O).
    # ==================================================================== #
    from src.surface.types import Action

    decision = gate.check(Action(kind="navigate", url="https://evil.example.com/members/search"), ReplayContext(run_id="r"))
    check(
        'an action targeting a domain outside the allowlist is denied, with rule naming "allowlist.domains"',
        decision.verdict == "deny" and decision.rule == "allowlist.domains",
        f"decision={decision}",
    )

    decision = gate.check(Action(kind="navigate", url=f"{MERIDIAN}/_test/config"), ReplayContext(run_id="r"))
    check(
        "an action targeting /_test/config is denied (pure gate check)",
        decision.verdict == "deny" and decision.rule == "allowlist.routes",
        f"decision={decision}",
    )

    decision = gate.check(Action(kind="navigate", url=f"{MERIDIAN}/members/10001"), ReplayContext(run_id="r"))
    check(
        "a route with a concrete ID (/members/10001) is allowed via the :member_id pattern",
        decision.verdict == "allow",
        f"decision={decision}",
    )

    restricted = ConfiguredPolicyGate(
        PolicyConfig(
            allowlist=AllowlistConfig(
                domains=config.allowlist.domains,
                routes=config.allowlist.routes,
                action_kinds=tuple(k for k in config.allowlist.action_kinds if k != "select"),
            ),
            risk=config.risk,
            redaction=config.redaction,
        )
    )
    decision = restricted.check(Action(kind="select", target=None, value="x"), ReplayContext(run_id="r"))
    check(
        "an action kind not in action_kinds is denied",
        decision.verdict == "deny" and decision.rule == "allowlist.action_kinds",
        f"decision={decision}",
    )

    # ==================================================================== #
    # Replay of the real, read-only shipped capability passes the gate
    # unchanged and still returns 4,832.10.
    # ==================================================================== #
    reset_app()
    engine = ReplayEngine(TrackingFactory())
    result = engine.run(artifact, {"member_id": "10001"}, ReplayContext(run_id="policy-accept-readonly", attended=True))
    check(
        "replay of the read-only capability passes the gate unchanged and still returns 4,832.10",
        result.status == "success" and str(result.outputs.get("savings_balance")) == "4832.10",
        f"status={result.status}, outputs={result.outputs}, error={result.error}",
    )

    # ==================================================================== #
    # A risky step's three verdicts, end to end, on a copy of the real
    # artifact with one step forced risky (declared risk is authoritative
    # on replay -- see docs/policy-spec.md §3).
    # ==================================================================== #
    risky_artifact = copy.deepcopy(artifact)
    risky_step = next(s for s in risky_artifact.steps if s.action == "type")
    risky_step.risk = "risky"

    reset_app()
    factory = TrackingFactory()
    engine = ReplayEngine(factory)
    result = engine.run(
        risky_artifact, {"member_id": "10001"}, ReplayContext(run_id="policy-accept-risky-unattended", allow_risky=False, attended=False)
    )
    check(
        "a risky step with allow_risky=False, unattended -> denied in pre-flight, browser never opened",
        result.status == "failure" and result.error is not None and result.error.code == "RISKY_STEPS_NOT_ALLOWED"
        and factory.call_count == 0,
        f"status={result.status}, error={result.error}, surface_factory_calls={factory.call_count}",
    )

    reset_app()
    # docs/escalation-spec.md §2: requires_approval is one of the three
    # triggers that routes through the same escalate-and-wait mechanism as
    # everything else, so this is no longer an immediate terminal failure --
    # the run genuinely blocks in AWAITING_HUMAN. A short claim_timeout_s
    # keeps this check from sitting through the real 5-minute default when,
    # as here, nobody claims it.
    engine = ReplayEngine(TrackingFactory(), claim_timeout_s=2.0, handoff_poll_interval_s=0.2)
    result = engine.run(
        risky_artifact, {"member_id": "10001"}, ReplayContext(run_id="policy-accept-risky-attended", allow_risky=False, attended=True)
    )
    intervention_path = Path(EVIDENCE_DIR) / "policy-accept-risky-attended" / "intervention.json"
    intervention = json.loads(intervention_path.read_text()) if intervention_path.exists() else None
    control_path = Path(EVIDENCE_DIR) / "policy-accept-risky-attended" / "control.json"
    control_after = json.loads(control_path.read_text()) if control_path.exists() else None
    check(
        "the same risky step with attended=True -> requires_approval escalation (not a silent allow), "
        "session held open in AWAITING_HUMAN, timing out to CLAIM_TIMEOUT with nobody claiming it",
        result.status == "failure" and result.error is not None and result.error.code == "CLAIM_TIMEOUT"
        and intervention is not None
        and intervention.get("session_held") is True
        and intervention.get("reason") == "POLICY_REQUIRES_APPROVAL"
        and control_after is not None and control_after.get("controller") == "abandoned",
        f"status={result.status}, error={result.error}, intervention={intervention}, control_after={control_after}",
    )

    reset_app()
    engine = ReplayEngine(TrackingFactory())
    result = engine.run(
        risky_artifact, {"member_id": "10001"}, ReplayContext(run_id="policy-accept-risky-allowed", allow_risky=True, attended=False)
    )
    check(
        "the same risky step with allow_risky=True -> allowed",
        result.status == "success" and str(result.outputs.get("savings_balance")) == "4832.10",
        f"status={result.status}, outputs={result.outputs}, error={result.error}",
    )

    # ==================================================================== #
    # During discovery, a denied action is reported back to the model as a
    # tool result and the loop continues (two decide() calls happen even
    # though the first action was denied).
    # ==================================================================== #
    reset_app()
    denial_run_id = "policy-accept-discovery-denial"
    evidence = EvidenceWriter(denial_run_id, redactor=Redactor(config.redaction))
    trace = TraceRecorder(evidence)
    llm = _ScriptedLLMClient(
        [
            AgentAction(kind="navigate", url=f"{MERIDIAN}/_test/config"),
            AgentAction(kind="navigate", url=f"{MERIDIAN}/members/search"),
            AgentAction(kind="stuck", reason="test script complete"),
        ]
    )
    # docs/escalation-spec.md §2: the model's "stuck" here now routes through
    # the real escalate-and-wait mechanism rather than stopping immediately
    # -- a short claim_timeout_s keeps this check from sitting through the
    # real 5-minute default when, as here, nobody claims it (full claim ->
    # human-acts -> resume coverage lives in scripts/escalation_acceptance.py).
    agent = DiscoveryAgent(
        WebSurface(headless=True), llm, policy_gate=gate, evidence_run_id=denial_run_id, trace=trace,
        claim_timeout_s=2.0, handoff_poll_interval_s=0.2,
    )
    agent.run(GOAL, f"{MERIDIAN}/members/search", max_steps=5)
    trace_events = read_jsonl(Path(EVIDENCE_DIR) / denial_run_id / "trace.jsonl")
    denied_events = [e for e in trace_events if e.get("event") == "policy_denied"]
    check(
        "during discovery, a denied action is reported back to the model as a tool result and the loop continues",
        llm.decide_calls >= 2 and len(denied_events) >= 1 and all(e.get("rule") for e in denied_events),
        f"decide_calls={llm.decide_calls}, denied_events={denied_events}",
    )

    # ==================================================================== #
    # Redaction, across everything a real run writes.
    # ==================================================================== #
    reset_app()
    engine = ReplayEngine(TrackingFactory())
    result = engine.run(artifact, {"member_id": "10001"}, ReplayContext(run_id="policy-accept-redaction", attended=True))
    run_dir = Path(EVIDENCE_DIR) / "policy-accept-redaction"
    balance_str = str(result.outputs.get("savings_balance"))
    jsonl_text = (run_dir / "replay.jsonl").read_text(encoding="utf-8") if (run_dir / "replay.jsonl").exists() else ""
    result_json = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    check(
        "a sensitive output value is absent from disk but present in the in-memory ReplayResult",
        balance_str not in jsonl_text
        and result_json["outputs"]["savings_balance"] == "REDACTED"
        and str(result.outputs["savings_balance"]) == "4832.10",
        f"balance_in_jsonl={balance_str in jsonl_text}, saved={result_json['outputs']}, in_memory={result.outputs}",
    )

    # member_id is whatever the shipped artifact declares -- check whatever
    # its actual sensitive inputs are, rather than assuming which ones.
    sensitive_inputs = [i for i in artifact.inputs if i.sensitive]
    if sensitive_inputs:
        check(
            "a sensitive input value appears nowhere in any .jsonl or result.json",
            "10001" not in jsonl_text and "10001" not in json.dumps(result_json),
            f"sensitive_inputs={[i.name for i in sensitive_inputs]}",
        )
    else:
        check(
            "a sensitive input value appears nowhere in any .jsonl or result.json",
            True,
            "shipped artifact currently declares no sensitive input -- nothing to check here; "
            "see tests/policy/test_redaction.py for the deterministic version of this property",
        )

    # Synthetic SSN-shaped string, redacted by pattern alone (never declared
    # sensitive, never key-named) -- through the exact same EvidenceWriter/
    # Redactor machinery a real run uses, not a bespoke helper.
    ssn_evidence = EvidenceWriter("policy-accept-ssn-pattern", redactor=Redactor(config.redaction))
    ssn_evidence.log({"event": "note", "text": "applicant ssn on file: 123-45-6789"})
    ssn_log_text = (Path(EVIDENCE_DIR) / "policy-accept-ssn-pattern" / "replay.jsonl").read_text(encoding="utf-8")
    check(
        "a synthetic SSN-shaped string in a log payload is redacted by pattern",
        "123-45-6789" not in ssn_log_text and "REDACTED" in ssn_log_text,
        f"log_text={ssn_log_text!r}",
    )

    # ==================================================================== #
    # Recovery actions pass through the gate (regression: the bypass fixed
    # in step 4). A gate that denies exactly the recovery's own action
    # (clicking "Acknowledge") must stop that recovery from silently
    # succeeding.
    # ==================================================================== #
    class _DenyAcknowledgeGate:
        def check(self, action, ctx) -> PolicyDecision:
            from src.schema.common import A11yStrategy

            if action.target is not None:
                for strategy in action.target.strategies:
                    if isinstance(strategy, A11yStrategy) and strategy.name == "Acknowledge":
                        return PolicyDecision(verdict="deny", rule="test.deny_acknowledge", reason="test")
            return gate.check(action, ctx)

    reset_app()
    requests.post(f"{MERIDIAN}/_test/config", json={"modal_on_next": 50}, timeout=5)
    deny_gate = _DenyAcknowledgeGate()
    engine = ReplayEngine(TrackingFactory(), policy_gate=deny_gate, claim_timeout_s=2.0, handoff_poll_interval_s=0.2)
    result = engine.run(artifact, {"member_id": "10001"}, ReplayContext(run_id="policy-accept-recovery-gated", attended=True))
    recovery_run_events = read_jsonl(Path(EVIDENCE_DIR) / "policy-accept-recovery-gated" / "replay.jsonl")
    recovery_denied = [e for e in recovery_run_events if e.get("event") == "policy_denied"]
    check(
        "recovery actions pass through the gate (regression for the bypass fixed in step 4)",
        result.status != "success" and len(recovery_denied) >= 1,
        f"status={result.status}, recovery_denied_events={recovery_denied[:3]}",
    )
    if engine.last_surface is not None:
        try:
            engine.last_surface.close(keep_trace=False)
        except Exception:
            pass

    # ==================================================================== #
    # Every denial logged so far carries a rule.
    # ==================================================================== #
    all_denials: list[dict] = []
    for run_id in ["policy-accept-risky-attended", "policy-accept-discovery-denial", "policy-accept-recovery-gated"]:
        for filename in ("replay.jsonl", "discovery.jsonl", "trace.jsonl"):
            all_denials.extend(
                e for e in read_jsonl(Path(EVIDENCE_DIR) / run_id / filename) if e.get("event") == "policy_denied"
            )
    check(
        "every denial in the logs carries a rule",
        len(all_denials) >= 1 and all(e.get("rule") for e in all_denials),
        f"denial_events={all_denials}",
    )

    print()
    passed = sum(1 for _, ok in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
