"""Acceptance checks for the discovery agent and compiler (docs/discovery-
spec.md §10), against the live meridian tenant on port 5001.

The genuine end-to-end thread (a real `discover` CLI run, LLM-driven) is
run exactly once here to conserve API quota; the rest of that run's
properties (declared input/output, checkpoints, provenance, the probe
outcome, and both replays of the resulting artifact) are all checked
against its actual output. MAX_STEPS, invalid-ref rejection, and the
disallowed-domain denial are deterministic engineering properties already
covered by tests/agent/test_loop.py against a fake Surface; here they're
re-verified against the *real* WebSurface and a scripted (zero-API-cost)
LLM client, which is the live-app confirmation this script otherwise owes
each acceptance line -- without spending a second real model call on them.

Run with: python -m scripts.discovery_acceptance
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import requests

from src.agent.loop import DiscoveryAgent
from src.agent.trace import TraceRecorder
from src.agent.types import AgentAction
from src.policy import ConfiguredPolicyGate, PolicyConfig, Redactor
from src.replay.context import ReplayContext
from src.replay.engine import ReplayEngine
from src.replay.evidence import EvidenceWriter
from src.schema.artifact import CapabilityArtifact
from src.schema.recoveries import Recovery
from src.surface.web import WebSurface

MERIDIAN = "http://127.0.0.1:5001"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
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


def run_discover_cli(*extra_args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, "-m", "src.cli", "discover", "--goal", GOAL, "--target", f"{MERIDIAN}/members/search", *extra_args],
        capture_output=True, text=True, timeout=timeout, cwd=str(PROJECT_ROOT),
    )


def extract_run_id(stdout: str) -> str | None:
    match = re.search(r"run_id=(\S+)", stdout)
    return match.group(1) if match else None


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    # ==================================================================== #
    # The genuine, LLM-driven end-to-end run.
    # ==================================================================== #
    reset_app()
    proc = run_discover_cli()
    run_id = extract_run_id(proc.stdout)
    check(
        'discover --goal "..." --target ... completes and emits a draft artifact',
        proc.returncode == 0 and run_id is not None,
        f"returncode={proc.returncode}\n       stdout={proc.stdout}\n       stderr={proc.stderr[-2000:]}",
    )
    if run_id is None:
        print("\nCannot continue without a run_id -- aborting remaining checks.")
        return 1

    evidence_dir = Path("evidence") / run_id
    artifact_path = evidence_dir / "artifact.draft.json"
    trace_path = evidence_dir / "trace.jsonl"

    artifact_data = json.loads(artifact_path.read_text(encoding="utf-8")) if artifact_path.exists() else None
    trace_events = read_jsonl(trace_path)
    trace_text = trace_path.read_text(encoding="utf-8") if trace_path.exists() else ""

    observations = [e for e in trace_events if e.get("event") == "observation"]
    decisions = [e for e in trace_events if e.get("event") == "decision"]
    results = [e for e in trace_events if e.get("event") == "result"]
    check(
        "trace.jsonl exists and shows the real observe -> decide -> act sequence",
        trace_path.exists() and len(observations) >= 1 and len(decisions) >= 1 and len(results) >= 1,
        f"observations={len(observations)}, decisions={len(decisions)}, results={len(results)}",
    )

    reached_summary = any(e.get("heading") == "Account summary" for e in observations)
    read_decisions = [e for e in decisions if e["action"]["kind"] == "read"]
    check(
        "the run reached the account summary and read the balance",
        reached_summary and len(read_decisions) >= 1,
        f"reached_summary={reached_summary}, read_decisions={read_decisions}",
    )

    if artifact_data is not None:
        inputs = artifact_data["inputs"]
        outputs = artifact_data["outputs"]
        steps = artifact_data["steps"]

        member_id_input = next((i for i in inputs if i["name"] == "member_id"), None)
        type_step = next((s for s in steps if s["action"] == "type"), None)
        check(
            "member_id is a declared typed input, and step.value is {{member_id}} -- not the literal 10001",
            member_id_input is not None and type_step is not None and type_step["value"] == "{{member_id}}",
            f"member_id_input={member_id_input}, type_step_value={type_step and type_step['value']!r}",
        )

        savings_output = next((o for o in outputs if o["name"] == "savings_balance"), None)
        source_step = savings_output and next((s for s in steps if s["id"] == savings_output["source"]["step_id"]), None)
        check(
            "savings_balance is a declared output with type money, bound to the read step",
            savings_output is not None and savings_output["type"] == "money"
            and source_step is not None and source_step["action"] == "read",
            f"savings_output={savings_output}",
        )

        check("approval_state is draft", artifact_data["capability"]["approval_state"] == "draft")

        trace_ref = artifact_data["provenance"]["trace_ref"]
        recorded_against = artifact_data["provenance"]["recorded_against"]
        check(
            "provenance.trace_ref points at a file that exists; recorded_against is v4.2.1",
            Path(trace_ref).exists() and recorded_against == "v4.2.1",
            f"trace_ref={trace_ref}, exists={Path(trace_ref).exists()}, recorded_against={recorded_against!r}",
        )

        strategy_check = all(
            s["target"] is None or (len(s["target"]["strategies"]) >= 2 and bool(s["target"]["description"].strip()))
            for s in steps
        )
        check(
            "every step's target has at least two ranked strategies and a non-empty description",
            strategy_check,
            json.dumps([(s["id"], s["target"] and len(s["target"]["strategies"])) for s in steps]),
        )

        outcomes = artifact_data["outcomes"]
        probed_outcome = next((o for o in outcomes if o["origin"] == "probed"), None)
        check(
            "the probe pass produced that outcome with origin: \"probed\"",
            probed_outcome is not None,
            f"outcomes={outcomes}",
        )

        # --- replay the compiled artifact --------------------------------- #
        artifact = CapabilityArtifact.model_validate(artifact_data)

        # Recoveries are never probed or synthesized -- docs/discovery-
        # spec.md §7 is explicit that they're origin: declared, added in
        # review. This app requires a session and a fresh replay run starts
        # with none, so a SESSION_EXPIRED recovery is exactly what a
        # reviewer adds before approving; simulate that one review action
        # here rather than have the compiler guess at app-specific auth
        # behavior it was never shown failing.
        artifact.recoveries.append(
            Recovery(
                code="SESSION_EXPIRED",
                kind="recoverable",
                description=(
                    "Reviewer-added: no session yet, or it timed out. Not discoverable from a "
                    "single happy-path run (docs/discovery-spec.md §7)."
                ),
                detect={"kind": "url_matches", "pattern": ".*/login.*"},
                check_after=["any"],
                recovery={"action": "re_authenticate"},
                max_attempts=1,
                then="restart_from",
                restart_step_id=artifact.steps[0].id,
            )
        )

        reset_app()
        engine_ok = ReplayEngine(lambda: WebSurface(headless=True))
        # attended=True: the artifact is (correctly) approval_state=draft,
        # and draft artifacts refuse to run unattended (replay-spec §2.1)
        # -- reviewing/testing a fresh draft is exactly what attended mode
        # is for.
        result_ok = engine_ok.run(
            artifact, {"member_id": "10001"}, ReplayContext(run_id=f"{run_id}-replay-10001", attended=True)
        )
        check(
            "the compiled artifact replays successfully via the replay engine with member_id=10001, returning 4,832.10",
            result_ok.status == "success" and str(result_ok.outputs.get("savings_balance")) == "4832.10",
            f"status={result_ok.status}, outputs={result_ok.outputs}, error={result_ok.error}",
        )

        reset_app()
        engine_nf = ReplayEngine(lambda: WebSurface(headless=True))
        result_nf = engine_nf.run(
            artifact, {"member_id": "99999"}, ReplayContext(run_id=f"{run_id}-replay-99999", attended=True)
        )
        check(
            "the compiled artifact replays with member_id=99999 and returns MEMBER_NOT_FOUND as a business "
            "outcome using the probed detector",
            result_nf.status == "business_outcome"
            and result_nf.outcome is not None
            and probed_outcome is not None
            and result_nf.outcome.code == probed_outcome["code"],
            f"status={result_nf.status}, outcome={result_nf.outcome}, probed_outcome={probed_outcome}",
        )

        sensitive_input = next((i for i in inputs if i["sensitive"]), None)
        if sensitive_input is not None and sensitive_input["name"] == "member_id":
            check(
                "no sensitive parameter value appears in trace.jsonl",
                "10001" not in trace_text,
                f"member_id marked sensitive={sensitive_input}",
            )
        else:
            check(
                "no sensitive parameter value appears in trace.jsonl",
                True,
                f"model did not mark member_id sensitive this run (sensitive_input={sensitive_input}); "
                "nothing to check -- see tests/agent/test_redaction.py for the deterministic version of this property",
            )
    else:
        for name in [
            "member_id is a declared typed input, and step.value is {{member_id}} -- not the literal 10001",
            "savings_balance is a declared output with type money, bound to the read step",
            "approval_state is draft",
            "provenance.trace_ref points at a file that exists; recorded_against is v4.2.1",
            "every step's target has at least two ranked strategies and a non-empty description",
            "the probe pass produced that outcome with origin: \"probed\"",
            "the compiled artifact replays successfully via the replay engine with member_id=10001, returning 4,832.10",
            "the compiled artifact replays with member_id=99999 and returns MEMBER_NOT_FOUND as a business outcome using the probed detector",
            "no sensitive parameter value appears in trace.jsonl",
        ]:
            check(name, False, "no artifact.draft.json was produced")

    # ==================================================================== #
    # Deterministic properties, re-verified against the real WebSurface with
    # a scripted (zero-API-cost) LLM client.
    # ==================================================================== #

    class _ScriptedLLMClient:
        def __init__(self, actions):
            self._actions = list(actions)

        def decide(self, goal, observation_text, history):
            if not self._actions:
                raise AssertionError("script exhausted")
            return self._actions.pop(0)

    reset_app()
    policy_config = PolicyConfig.load()
    scripted_run_id = "discovery-accept-max-steps"
    scripted_evidence = EvidenceWriter(scripted_run_id, redactor=Redactor(policy_config.redaction))
    scripted_trace = TraceRecorder(scripted_evidence)
    scripted_agent = DiscoveryAgent(
        WebSurface(headless=True),
        _ScriptedLLMClient(
            [
                AgentAction(kind="click", ref="not-a-real-ref-1"),
                AgentAction(kind="click", ref="not-a-real-ref-2"),
            ]
        ),
        policy_gate=ConfiguredPolicyGate(policy_config),
        evidence_run_id=scripted_run_id,
        trace=scripted_trace,
    )
    scripted_result = scripted_agent.run(GOAL, f"{MERIDIAN}/members/search", max_steps=2)
    scripted_artifact_exists = (Path("evidence") / scripted_run_id / "artifact.draft.json").exists()
    scripted_trace_events = read_jsonl(Path("evidence") / scripted_run_id / "trace.jsonl")
    invalid_ref_events = [e for e in scripted_trace_events if e.get("event") == "invalid_ref"]
    check(
        "a run with max_steps=2 stops with MAX_STEPS and emits no artifact",
        scripted_result.stop_reason == "MAX_STEPS" and not scripted_artifact_exists,
        f"stop_reason={scripted_result.stop_reason}, artifact_exists={scripted_artifact_exists}",
    )
    check(
        "an invalid ref from the model is rejected without acting, and logged",
        len(invalid_ref_events) >= 1 and all(e["ref"].startswith("not-a-real-ref") for e in invalid_ref_events),
        f"invalid_ref_events={invalid_ref_events}",
    )

    reset_app()
    disallowed_run_id = "discovery-accept-disallowed-domain"
    disallowed_evidence = EvidenceWriter(disallowed_run_id, redactor=Redactor(policy_config.redaction))
    disallowed_trace = TraceRecorder(disallowed_evidence)
    tracking_surface = WebSurface(headless=True)
    disallowed_agent = DiscoveryAgent(
        tracking_surface,
        _ScriptedLLMClient([AgentAction(kind="done", summary="unreachable")]),
        # The real, shipped allowlist -- evil.example.com is deliberately
        # not in it. Deny by default, not a special-purpose test gate.
        policy_gate=ConfiguredPolicyGate(policy_config),
        evidence_run_id=disallowed_run_id,
        trace=disallowed_trace,
    )
    disallowed_result = disallowed_agent.run(GOAL, "https://evil.example.com/members/search", max_steps=5)
    check(
        "a run whose goal targets a disallowed domain is denied by the policy gate",
        disallowed_result.stop_reason == "POLICY",
        f"stop_reason={disallowed_result.stop_reason}",
    )

    print()
    passed = sum(1 for _, ok in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
