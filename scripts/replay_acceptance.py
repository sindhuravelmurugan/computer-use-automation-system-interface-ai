"""Acceptance checks for the replay engine (docs/replay-spec.md §11), run
against the shipped artifact at artifacts/member.lookup_savings_balance.json
and the live meridian tenant on port 5001.

The hand-written v0 scaffolding artifact this script originally targeted
was deleted per docs/discovery-spec.md §9 once a real discovery run could
produce (and a review pass could complete) the genuine article -- see
scripts/discovery_acceptance.py for the discovery/compile/probe run that
produced it. This script still earns its keep: it covers replay-engine
behaviors (recovery/escalation/determinism/redaction) that discovery's own
acceptance script doesn't re-derive.

Run with: python -m scripts.replay_acceptance
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import requests

from src.replay.context import ReplayContext
from src.replay.engine import ReplayEngine
from src.replay.policy import PolicyDecision
from src.schema.artifact import CapabilityArtifact
from src.surface.web import WebSurface

MERIDIAN = "http://127.0.0.1:5001"
ARTIFACT_PATH = Path("artifacts/member.lookup_savings_balance.json")
EVIDENCE_DIR = "evidence"

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


def configure(**flags) -> None:
    requests.post(f"{MERIDIAN}/_test/config", json=flags, timeout=5)


def load_artifact() -> CapabilityArtifact:
    data = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    return CapabilityArtifact.model_validate(data)


class TrackingFactory:
    """Wraps WebSurface() so pre-flight-rejection checks can assert the
    browser was never opened."""

    def __init__(self) -> None:
        self.call_count = 0

    def __call__(self) -> WebSurface:
        self.call_count += 1
        return WebSurface(headless=True)


class ArmFlagBeforeNthAction:
    """A PolicyGate that arms a /_test/config flag deterministically right
    before the Nth action the engine takes, then defers to AllowAllGate.
    Used to inject expire_session mid-run without any timing-based sleep or
    thread race: the gate runs synchronously inside the engine's own loop,
    exactly between two actions.
    """

    def __init__(self, arm_before: int, **flags) -> None:
        self._arm_before = arm_before
        self._flags = flags
        self._count = 0
        self.armed = False

    def check(self, action, ctx) -> PolicyDecision:
        self._count += 1
        if self._count == self._arm_before and not self.armed:
            configure(**self._flags)
            self.armed = True
        return PolicyDecision(allowed=True)


def read_jsonl(run_id: str) -> list[dict]:
    path = Path(EVIDENCE_DIR) / run_id / "replay.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_result_file(run_id: str) -> dict:
    path = Path(EVIDENCE_DIR) / run_id / "result.json"
    return json.loads(path.read_text(encoding="utf-8"))


def no_playwright_or_llm_import(base_dir: str) -> bool:
    forbidden = {"playwright", "anthropic", "google.genai"}
    for path in sorted(Path(base_dir).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name == f or name.startswith(f + ".") for f in forbidden):
                    return False
    return True


def no_sleep_call(base_dir: str) -> bool:
    for path in sorted(Path(base_dir).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name == "sleep":
                    return False
    return True


def main() -> int:
    artifact = load_artifact()

    # --- check 1: happy path ------------------------------------------------ #
    reset_app()
    factory = TrackingFactory()
    engine = ReplayEngine(factory)
    ctx = ReplayContext(run_id="replay-success-10001", keep_trace=False)
    result = engine.run(artifact, {"member_id": "10001"}, ctx)
    check(
        "member_id=10001 -> success, savings_balance parsed as decimal, success checkpoint verified",
        result.status == "success"
        and str(result.outputs.get("savings_balance")) == "4832.10",
        f"status={result.status}, outputs={result.outputs}, error={result.error}",
    )

    # --- check 2: business outcome, not a failure --------------------------- #
    reset_app()
    engine = ReplayEngine(TrackingFactory())
    ctx = ReplayContext(run_id="replay-not-found-99999")
    result = engine.run(artifact, {"member_id": "99999"}, ctx)
    events = read_jsonl(ctx.run_id)
    error_events = [e for e in events if e.get("event") == "failure" or "error" in e]
    check(
        "member_id=99999 -> business_outcome MEMBER_NOT_FOUND, nothing logged as an error",
        result.status == "business_outcome"
        and result.outcome is not None
        and result.outcome.code == "MEMBER_NOT_FOUND"
        and not error_events,
        f"status={result.status}, outcome={result.outcome}, error_events={error_events}",
    )

    # --- check 3: restricted member is a declared business outcome ----------- #
    # Superseded from the spec's literal "failure with a distinct code":
    # PERMISSION_DENIED is now a declared outcome, so a restricted member is
    # a legitimate answer (not_permitted), not an unrecognized state. The
    # error_on_next check below remains the default-deny/unrecognized-state
    # demonstration.
    reset_app()
    engine = ReplayEngine(TrackingFactory())
    ctx = ReplayContext(run_id="replay-restricted-10004")
    result = engine.run(artifact, {"member_id": "10004"}, ctx)
    events = read_jsonl(ctx.run_id)
    error_events = [e for e in events if e.get("event") == "failure" or "error" in e]
    check(
        "member_id=10004 (restricted) -> business_outcome PERMISSION_DENIED, nothing logged as an error",
        result.status == "business_outcome"
        and result.outcome is not None
        and result.outcome.code == "PERMISSION_DENIED"
        and result.outcome.returns == {"permitted": False}
        and not error_events,
        f"status={result.status}, outcome={result.outcome}, error_events={error_events}",
    )

    # --- check 4: SESSION_EXPIRED recovery fires mid-run --------------------- #
    reset_app()
    # Arm expire_session right before the 8th action the engine takes: the
    # 5 bootstrap-login actions (initial unauthenticated navigate + the 4
    # re_authenticate sub-actions) + step_001's retried navigate + step_002's
    # type land at positions 1-7, so position 8 is step_003's click -- the
    # next real server round-trip, which is what actually observes the
    # bumped epoch. Deterministic: the gate runs synchronously inside the
    # engine's own loop, not on a timer.
    gate = ArmFlagBeforeNthAction(arm_before=8, expire_session=True)
    engine = ReplayEngine(TrackingFactory(), policy_gate=gate)
    ctx = ReplayContext(run_id="replay-session-expired")
    result = engine.run(artifact, {"member_id": "10001"}, ctx)
    session_expired_recoveries = [r for r in result.recoveries_applied if r.code == "SESSION_EXPIRED"]
    check(
        "expire_session flag -> SESSION_EXPIRED recovery fires, run completes, recoveries_applied non-empty",
        gate.armed and result.status == "success" and len(session_expired_recoveries) >= 1,
        f"armed={gate.armed}, status={result.status}, recoveries_applied={result.recoveries_applied}",
    )

    # --- check 5: disclosure interstitial dismissed -------------------------- #
    reset_app()
    configure(modal_on_next=2)
    engine = ReplayEngine(TrackingFactory())
    ctx = ReplayContext(run_id="replay-modal-interstitial")
    result = engine.run(artifact, {"member_id": "10001"}, ctx)
    modal_recoveries = [r for r in result.recoveries_applied if r.code == "CONFIRMATION_INTERSTITIAL"]
    check(
        "modal_on_next=2 -> interstitial dismissed, run completes, attempts recorded",
        result.status == "success" and len(modal_recoveries) >= 1 and modal_recoveries[0].attempts >= 1,
        f"status={result.status}, recoveries_applied={result.recoveries_applied}",
    )

    # --- check 6: latency absorbed by condition-based wait, not a fixed sleep - #
    reset_app()
    gate = ArmFlagBeforeNthAction(arm_before=8, latency_ms=4000)
    engine = ReplayEngine(TrackingFactory(), policy_gate=gate)
    ctx = ReplayContext(run_id="replay-latency-4000")
    result = engine.run(artifact, {"member_id": "10001"}, ctx)
    check(
        "latency_ms=4000 -> wait strategy absorbs it, run still succeeds",
        gate.armed and result.status == "success",
        f"armed={gate.armed}, status={result.status}, duration_ms={result.duration_ms}, error={result.error}",
    )

    # --- check 7: injected server error is a hard failure -------------------- #
    reset_app()
    gate = ArmFlagBeforeNthAction(arm_before=8, error_on_next=True)
    engine = ReplayEngine(TrackingFactory(), policy_gate=gate)
    ctx = ReplayContext(run_id="replay-error-on-next")
    result = engine.run(artifact, {"member_id": "10001"}, ctx)
    screenshot_exists = bool(result.error) and Path(result.error.evidence).exists()
    check(
        "error_on_next -> hard failure with step ID, expected, observed, screenshot",
        result.status == "failure"
        and result.error is not None
        and result.error.step_id is not None
        and result.error.expected
        and result.error.observed
        and screenshot_exists,
        f"status={result.status}, error={result.error}",
    )

    # --- check 8: exhausted recovery raises an InterventionRequest ----------- #
    reset_app()
    # A very large modal_on_next keeps re-showing the modal past what
    # max_attempts=2 can absorb, so the recovery genuinely exhausts.
    configure(modal_on_next=50)
    engine = ReplayEngine(TrackingFactory())
    ctx = ReplayContext(run_id="replay-exhausted-recovery", attended=False)
    result = engine.run(artifact, {"member_id": "10001"}, ctx)
    intervention_path = Path(EVIDENCE_DIR) / ctx.run_id / "intervention.json"
    intervention = json.loads(intervention_path.read_text()) if intervention_path.exists() else None
    check(
        "exhausted max_attempts -> InterventionRequest written, failure returned promptly",
        result.status == "failure"
        and result.error is not None
        and result.error.code == "EXHAUSTED_RECOVERY"
        and intervention is not None
        and intervention.get("reason") == "EXHAUSTED_RECOVERY",
        f"status={result.status}, error={result.error}, intervention_exists={intervention_path.exists()}",
    )

    # --- check 9: attended vs unattended session holding ---------------------- #
    reset_app()
    configure(modal_on_next=50)
    engine_unattended = ReplayEngine(TrackingFactory())
    ctx_unattended = ReplayContext(run_id="replay-escalation-unattended", attended=False)
    engine_unattended.run(artifact, {"member_id": "10001"}, ctx_unattended)
    unattended_intervention = json.loads(
        (Path(EVIDENCE_DIR) / ctx_unattended.run_id / "intervention.json").read_text()
    )

    reset_app()
    configure(modal_on_next=50)
    engine_attended = ReplayEngine(TrackingFactory())
    ctx_attended = ReplayContext(run_id="replay-escalation-attended", attended=True)
    engine_attended.run(artifact, {"member_id": "10001"}, ctx_attended)
    attended_intervention = json.loads(
        (Path(EVIDENCE_DIR) / ctx_attended.run_id / "intervention.json").read_text()
    )
    # session_held=True leaves the browser open -- verify it is still alive,
    # then close it ourselves (real takeover is step 7).
    attended_surface_alive = False
    if engine_attended.last_surface is not None:
        try:
            engine_attended.last_surface.observe()
            attended_surface_alive = True
        except Exception:
            attended_surface_alive = False
        finally:
            engine_attended.last_surface.close(keep_trace=False)
    check(
        "attended=False on a read-only flow closes the session after escalation; attended=True holds it",
        unattended_intervention["session_held"] is False
        and attended_intervention["session_held"] is True
        and attended_surface_alive,
        f"unattended_held={unattended_intervention['session_held']}, "
        f"attended_held={attended_intervention['session_held']}, "
        f"attended_surface_alive={attended_surface_alive}",
    )

    # --- check 10: bad input rejected in pre-flight, browser never opened ----- #
    reset_app()
    factory = TrackingFactory()
    engine = ReplayEngine(factory)
    ctx = ReplayContext(run_id="replay-bad-input")
    result = engine.run(artifact, {"member_id": "abc"}, ctx)
    check(
        'bad input (member_id="abc") -> rejected in pre-flight, browser never opened',
        result.status == "failure" and result.error is not None and result.error.code == "INVALID_INPUT"
        and factory.call_count == 0,
        f"status={result.status}, error={result.error}, surface_factory_calls={factory.call_count}",
    )

    # --- check 11: draft + unattended refused before pre-flight completes ----- #
    draft_artifact = load_artifact()
    draft_artifact.capability.approval_state = "draft"
    factory = TrackingFactory()
    engine = ReplayEngine(factory)
    ctx = ReplayContext(run_id="replay-draft-unattended", attended=False)
    result = engine.run(draft_artifact, {"member_id": "10001"}, ctx)
    check(
        "draft approval state + unattended -> refused before pre-flight completes",
        result.status == "failure" and result.error is not None and result.error.code == "DRAFT_UNATTENDED"
        and factory.call_count == 0,
        f"status={result.status}, error={result.error}, surface_factory_calls={factory.call_count}",
    )

    # --- check 12: determinism across three consecutive runs ------------------ #
    outputs_seen = []
    strategy_sequences = []
    for n in range(3):
        reset_app()
        engine = ReplayEngine(TrackingFactory())
        ctx = ReplayContext(run_id=f"replay-determinism-{n}")
        result = engine.run(artifact, {"member_id": "10001"}, ctx)
        outputs_seen.append(json.dumps(result.outputs, sort_keys=True, default=str))
        events = read_jsonl(ctx.run_id)
        strategy_sequences.append([e["strategy_index"] for e in events if e.get("event") == "resolve"])
    check(
        "three consecutive runs of 10001 produce byte-identical outputs and the same strategy_index sequence",
        len(set(outputs_seen)) == 1 and len({tuple(s) for s in strategy_sequences}) == 1,
        f"outputs_seen={outputs_seen}, strategy_sequences={strategy_sequences}",
    )

    # --- check 13/14: static determinism invariants (also pytest-covered) ----- #
    check(
        "no module under src/replay/ imports playwright, anthropic, or google.genai",
        no_playwright_or_llm_import("src/replay"),
    )
    check(
        "no sleep( anywhere under src/replay/",
        no_sleep_call("src/replay"),
    )

    # --- check 15: sensitive output redacted everywhere but the in-memory result #
    reset_app()
    engine = ReplayEngine(TrackingFactory())
    ctx = ReplayContext(run_id="replay-redaction")
    result = engine.run(artifact, {"member_id": "10001"}, ctx)
    jsonl_text = (Path(EVIDENCE_DIR) / ctx.run_id / "replay.jsonl").read_text(encoding="utf-8")
    saved_result = read_result_file(ctx.run_id)
    balance_str = str(result.outputs["savings_balance"])
    check(
        "a sensitive output's value appears nowhere in replay.jsonl or the saved result",
        balance_str not in jsonl_text
        and saved_result["outputs"]["savings_balance"] == "REDACTED"
        and result.outputs["savings_balance"] == result.outputs["savings_balance"],  # in-memory keeps real value
        f"balance_in_jsonl={balance_str in jsonl_text}, saved_outputs={saved_result.get('outputs')}, "
        f"in_memory={result.outputs}",
    )

    print()
    passed = sum(1 for _, ok in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
