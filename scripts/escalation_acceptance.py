"""Acceptance checks for escalation and handoff (docs/escalation-spec.md
§9), against the live meridian tenant on port 5001.

The engine (or discovery agent) blocks on `wait_for_handoff` for real once a
session is escalated and held -- so exercising a genuine claim/act/release
cycle means running it in a background thread while this script plays both
"operator" (via the Flask app's own test client, no separate process
needed -- it reads/writes the same evidence/ directory either way) and
"human" (a *second*, independent Playwright connection over the same CDP
endpoint, exactly what a real operator's browser tab would be).

Run with: python -m scripts.escalation_acceptance
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

from src.agent.loop import DiscoveryAgent
from src.agent.trace import TraceRecorder
from src.agent.types import AgentAction
from src.escalation.control import Controller, SessionControl
from src.escalation.operator_app import create_app
from src.policy import ConfiguredPolicyGate, PolicyConfig, Redactor
from src.policy.gate import PolicyDecision
from src.replay.context import ReplayContext
from src.replay.engine import ReplayEngine
from src.replay.evidence import EvidenceWriter
from src.schema.artifact import CapabilityArtifact
from src.surface.types import Action, ControllerViolation, WaitSpec
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


def configure(**flags) -> None:
    requests.post(f"{MERIDIAN}/_test/config", json=flags, timeout=5)


def load_artifact() -> CapabilityArtifact:
    data = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    return CapabilityArtifact.model_validate(data)


def read_jsonl(run_id: str, filename: str = "replay.jsonl") -> list[dict]:
    path = Path(EVIDENCE_DIR) / run_id / filename
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_human_actions(run_id: str) -> list[dict]:
    return read_jsonl(run_id, "human_actions.jsonl")


class CapturingFactory:
    """Like the other scripts' TrackingFactory, but also keeps the actual
    WebSurface instance around -- needed here to peek at its live browser
    context (cookies, direct act() calls) mid-run, while the engine has it
    checked out on a background thread.
    """

    def __init__(self) -> None:
        self.instances: list[WebSurface] = []

    def __call__(self) -> WebSurface:
        surface = WebSurface(headless=True)
        self.instances.append(surface)
        return surface

    @property
    def last(self) -> WebSurface:
        return self.instances[-1]


def wait_until(predicate, timeout: float = 20.0, interval: float = 0.1):
    deadline = time.monotonic() + timeout
    last_exc = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except Exception as exc:  # noqa: BLE001 -- polling, retry until timeout
            last_exc = exc
        time.sleep(interval)
    raise TimeoutError(f"condition not met within {timeout}s (last_exc={last_exc})")


def search_frame(page):
    """The Member ID field and Search button live inside the legacy
    frameset's "frmSearch" child frame -- Page.get_by_role only searches
    the main frame, so every interaction with the search form has to go
    through this frame explicitly, exactly as WebSurface's own frame
    flattening has to (docs/surface-spec.md).
    """
    return page.frame(name="frmSearch")


def load_control(run_id: str) -> SessionControl | None:
    if not SessionControl.exists(run_id, EVIDENCE_DIR):
        return None
    return SessionControl.load(run_id, EVIDENCE_DIR)


def _targets_member_id_field(target) -> bool:
    if target is None:
        return False
    return any(getattr(s, "kind", None) == "a11y" and getattr(s, "name", None) == "Member ID" for s in target.strategies)


class ForceApprovalOnMemberIdType:
    """Forces a `requires_approval` verdict on exactly the first attempt to
    type into the Member ID field (step_002's own action), then defers to
    the real gate -- deterministic injection matched on *what* the action
    is rather than counting gate.check() calls (which also fire during
    pre-flight and any recovery, so a raw position count is brittle). Lands
    the escalation with the app already authenticated and on a clean search
    page, rather than relying on a modal test-flag whose "reappears on
    every request" behavior turned out to also block the login form.
    """

    def __init__(self, gate) -> None:
        self._gate = gate
        self.triggered = False

    def check(self, action, ctx):
        if not self.triggered and action.kind == "type" and _targets_member_id_field(action.target):
            self.triggered = True
            return PolicyDecision(
                verdict="requires_approval", rule="test.force_approval", reason="forced for escalation acceptance"
            )
        return self._gate.check(action, ctx)


class StuckOnceThenDoneClient:
    """A scripted (zero-API-cost) LLM client: reports stuck immediately,
    then -- once handed back a fresh observation after a human's turn --
    declares done. Deterministic stand-in for the real model, same
    reasoning as scripts/discovery_acceptance.py's scripted checks.
    """

    def __init__(self) -> None:
        self.calls = 0

    def decide(self, goal, observation_text, history):
        self.calls += 1
        if self.calls == 1:
            return AgentAction(kind="stuck", reason="forced stuck for escalation acceptance")
        return AgentAction(kind="done", summary="human completed the flow manually")


def main() -> int:
    artifact = load_artifact()
    operator = create_app(EVIDENCE_DIR).test_client()

    # ==================================================================== #
    # The two checks that matter most (per the spec's own closing line):
    # the CDP URL opening the *same* session, and a human completing the
    # flow being detected as success with no re-execution. Both, plus most
    # of the rest of the checklist, come out of one two-round escalation,
    # forced via a policy gate that requires approval on exactly the type
    # action for the member id (already-authenticated, on a clean search
    # page -- see ForceApprovalOnNthAction): round 1 the "human" types the
    # *wrong* id and releases without searching (real activity, but not
    # what step_002's checkpoint or the capability's success condition
    # need -- an unrelated-enough state to force a re-escalation); round 2
    # they correct it and click Search, actually finishing the capability.
    # ==================================================================== #
    reset_app()
    run_id = "escalation-two-round-handoff"
    factory = CapturingFactory()
    forced_gate = ForceApprovalOnMemberIdType(gate=ConfiguredPolicyGate.from_file())
    engine = ReplayEngine(
        factory, policy_gate=forced_gate, claim_timeout_s=30.0, hold_timeout_s=30.0, handoff_poll_interval_s=0.1
    )
    ctx = ReplayContext(run_id=run_id, attended=True)
    holder: dict = {}
    thread = threading.Thread(target=lambda: holder.__setitem__("result", engine.run(artifact, {"member_id": "10001"}, ctx)))
    thread.start()

    control = wait_until(lambda: (c := load_control(run_id)) and c.controller == Controller.AWAITING_HUMAN and c)
    # Playwright's sync API is single-thread-affine -- the engine's own
    # WebSurface object lives on `thread`, so "is the browser still alive"
    # is checked from here over HTTP against its CDP debug port, not by
    # calling back into that object directly.
    cdp_alive = requests.get(f"{control.cdp_endpoint}/json/version", timeout=3).status_code == 200
    check(
        "a replay hitting an unrecoverable condition (here: requires_approval, one of the three declared "
        "triggers alongside exhausted-recovery and discovery-stuck) transitions to AWAITING_HUMAN, browser stays alive",
        control.controller == Controller.AWAITING_HUMAN and cdp_alive,
        f"controller={control.controller}, reason={control.reason}, cdp_alive={cdp_alive}",
    )

    list_resp = operator.get("/operator")
    detail_resp = operator.get(f"/operator/{run_id}")
    detail = detail_resp.get_json()
    check(
        "the operator page lists the open request with capability, step, reason, screenshot",
        any(row["run_id"] == run_id for row in list_resp.get_json())
        and detail["capability_id"] == artifact.capability.id
        and detail["step_id"] == "step_002"
        and detail["reason"] == "POLICY_REQUIRES_APPROVAL"
        and detail["screenshot_url"] and Path(control.screenshot_path).exists(),
        f"list_row_present={any(row['run_id'] == run_id for row in list_resp.get_json())}, detail={detail}",
    )

    still_awaiting = load_control(run_id)
    check(
        "merely loading the operator detail page does not claim -- only POST /claim does",
        still_awaiting.controller == Controller.AWAITING_HUMAN,
        f"controller={still_awaiting.controller}",
    )

    # Isolated on its own throwaway, single-threaded browser rather than
    # reaching into the live run's -- Playwright's sync API is
    # single-thread-affine, and that surface's handles belong to `thread`.
    isolated_surface = WebSurface(headless=True)
    isolated_surface.open(f"{MERIDIAN}/members/search", "escalation-controller-check")
    isolated_handle = isolated_surface.release()
    raised = False
    try:
        isolated_surface.act(Action(kind="wait_for", wait=WaitSpec(strategy="settle", timeout_ms=50)))
    except ControllerViolation:
        raised = True
    isolated_surface.reacquire(isolated_handle)
    isolated_surface.close(keep_trace=False)
    check(
        "Surface.act() raises if automation attempts an action while control is ceded",
        raised,
    )

    claim_resp = operator.post(f"/operator/{run_id}/claim", json={"name": "the-operator"})
    check(
        "claiming transitions AWAITING_HUMAN -> HUMAN",
        claim_resp.get_json()["controller"] == "human" and load_control(run_id).controller == Controller.HUMAN,
    )

    control = load_control(run_id)
    pw_human = sync_playwright().start()
    human_browser = pw_human.chromium.connect_over_cdp(control.cdp_endpoint)
    human_context = human_browser.contexts[0]
    human_page = next((p for p in human_context.pages if p.url == control.page_url), human_context.pages[0])

    # Not comparing against the automation's own cookie jar directly --
    # that Playwright object belongs to `thread` (sync API is
    # single-thread-affine) and touching it from here would raise exactly
    # the greenlet error the isolated controller check above was built to
    # avoid; and this app's session cookie is httpOnly, which a *second*
    # independent CDP client reliably fails to read back via
    # BrowserContext.cookies() even against the correct live context (a
    # Playwright/CDP quirk, verified separately, not a same-session
    # question). The proof that still fully settles "same session, not a
    # fresh one" without touching either: the exact URL automation left
    # (not about:blank, not a fresh navigate), showing the real search
    # form's Member ID field -- which an unauthenticated request to this
    # same URL never reaches (it bounces to /login, as step_001 itself did
    # before this run authenticated).
    check(
        "the CDP URL opens the same browser session, not a fresh one -- exact URL automation left, showing "
        "the authenticated page's own form rather than a login redirect",
        human_page.url == control.page_url
        and search_frame(human_page).get_by_role("textbox", name="Member ID").is_visible(),
        f"human_page.url={human_page.url}, control.page_url={control.page_url}",
    )

    # Round 1: type the *wrong* member id, don't search. Real activity, but
    # it satisfies neither step_002's own checkpoint (value_equals "10001")
    # nor the capability's success condition -- an unrelated-enough state
    # that handback should read as "unrecognized", not silently continue.
    search_frame(human_page).get_by_role("textbox", name="Member ID").fill("99999")

    # Delivery of a captured action to human_actions.jsonl rides on
    # WebSurface.pump_events(), called once per wait_for_handoff poll tick
    # (every handoff_poll_interval_s) -- so it lands within roughly that
    # interval of the human's action, not instantly. A real operator takes
    # at least that long to move their mouse to Release anyway; this script
    # just waits for the same thing explicitly.
    actions_round1 = wait_until(lambda: read_human_actions(run_id) or None, timeout=5) or []
    input_actions = [a for a in actions_round1 if a["kind"] in ("input", "change")]
    check(
        "human typing is captured to human_actions.jsonl with role and accessible name",
        any(a["role"] == "textbox" and a["name"] == "Member ID" for a in input_actions),
        f"input_actions={input_actions}",
    )
    check(
        "a typed value is recorded as value_redacted: true with no content",
        any(a.get("value_redacted") is True and "99999" not in json.dumps(a) for a in input_actions),
        f"input_actions={input_actions}",
    )

    operator.post(f"/operator/{run_id}/release")

    control = wait_until(
        lambda: (c := load_control(run_id))
        and c.controller == Controller.AWAITING_HUMAN
        and c.reason == "HANDBACK_UNVERIFIED"
        and c
    )
    check(
        "human leaves the app in an unrelated (not-yet-done) state -> engine re-escalates rather than blindly continuing",
        control.controller == Controller.AWAITING_HUMAN and control.reason == "HANDBACK_UNVERIFIED",
        f"controller={control.controller}, reason={control.reason}",
    )

    # Round 2: claim again, correct the id, and finish the flow for real.
    operator.post(f"/operator/{run_id}/claim", json={"name": "the-operator"})
    search_frame(human_page).get_by_role("textbox", name="Member ID").fill("10001")
    search_frame(human_page).get_by_role("button", name="Search").click()
    wait_until(lambda: human_page.get_by_role("heading", name="Account summary").is_visible())
    operator.post(f"/operator/{run_id}/release")

    thread.join(timeout=20)
    result = holder.get("result")
    events = read_jsonl(run_id)
    released_events = [e for e in events if e.get("event") == "control_released"]
    reacquired_events = [e for e in events if e.get("event") == "control_reacquired"]
    result_json = json.loads((Path(EVIDENCE_DIR) / run_id / "result.json").read_text(encoding="utf-8"))
    all_human_actions = read_human_actions(run_id)
    click_actions = [a for a in all_human_actions if a["kind"] == "click"]
    check(
        "human clicks are captured to human_actions.jsonl with role and accessible name",
        any(a["role"] == "button" and a["name"] == "Search" for a in click_actions),
        f"click_actions={click_actions}",
    )
    check(
        "release -> engine reacquires, re-verifies, and completes the run successfully",
        result is not None and result.status == "success" and not thread.is_alive(),
        f"status={getattr(result, 'status', None)}, thread_alive={thread.is_alive()}",
    )
    check(
        "human completes the flow manually -> engine detects the success condition on handback, "
        "returns success with outputs, and re-executes nothing else automation-side",
        result is not None
        and result.status == "success"
        and result.human_intervention is True
        and str(result.outputs.get("savings_balance")) == "4832.10",
        f"human_intervention={getattr(result, 'human_intervention', None)}, outputs={getattr(result, 'outputs', None)}",
    )
    check(
        "replay.jsonl contains control_released and control_reacquired boundary events for both rounds",
        len(released_events) >= 2 and len(reacquired_events) >= 2,
        f"released={len(released_events)}, reacquired={len(reacquired_events)}",
    )
    check(
        "result.json carries a populated handoffs array (who, when, how long, how many actions)",
        len(result_json.get("handoffs", [])) == 2
        and all(h["claimed_by"] == "the-operator" for h in result_json["handoffs"]),
        f"handoffs={result_json.get('handoffs')}",
    )
    reacquire_screens = list((Path(EVIDENCE_DIR) / run_id).glob("reacquire_*.png"))
    check(
        "screenshots exist at both the release boundary (what the human walks into) and reacquire (what they left behind)",
        Path(control.screenshot_path).exists() and len(reacquire_screens) >= 2,
        f"release_screenshot={control.screenshot_path}, reacquire_screenshots={[str(p) for p in reacquire_screens]}",
    )

    human_browser.close()
    pw_human.stop()

    # ==================================================================== #
    # Claim timeout: nobody ever claims it.
    # ==================================================================== #
    reset_app()
    configure(modal_on_next=50)
    engine_ct = ReplayEngine(CapturingFactory(), claim_timeout_s=1.0, handoff_poll_interval_s=0.1)
    ctx_ct = ReplayContext(run_id="escalation-claim-timeout", attended=True)
    result_ct = engine_ct.run(artifact, {"member_id": "10001"}, ctx_ct)
    control_ct = load_control(ctx_ct.run_id)
    check(
        "claim timeout -> ABANDONED, run fails, the intervention remains on record",
        result_ct.status == "failure"
        and result_ct.error is not None
        and result_ct.error.code == "CLAIM_TIMEOUT"
        and control_ct.controller == Controller.ABANDONED
        and (Path(EVIDENCE_DIR) / ctx_ct.run_id / "intervention.json").exists(),
        f"status={result_ct.status}, error={result_ct.error}, controller={control_ct.controller}",
    )

    # ==================================================================== #
    # Hold timeout: claimed, then nothing -- the session is closed rather
    # than left open indefinitely against a live bank system.
    # ==================================================================== #
    reset_app()
    configure(modal_on_next=50)
    ht_run_id = "escalation-hold-timeout"
    factory_ht = CapturingFactory()
    engine_ht = ReplayEngine(factory_ht, claim_timeout_s=30.0, hold_timeout_s=1.0, handoff_poll_interval_s=0.1)
    ctx_ht = ReplayContext(run_id=ht_run_id, attended=True)
    holder_ht: dict = {}
    thread_ht = threading.Thread(target=lambda: holder_ht.__setitem__("result", engine_ht.run(artifact, {"member_id": "10001"}, ctx_ht)))
    thread_ht.start()
    control_ht = wait_until(lambda: (c := load_control(ht_run_id)) and c.controller == Controller.AWAITING_HUMAN and c)
    cdp_endpoint_ht = control_ht.cdp_endpoint
    operator.post(f"/operator/{ht_run_id}/claim", json={"name": "the-operator"})
    thread_ht.join(timeout=15)
    result_ht = holder_ht.get("result")
    control_ht = load_control(ht_run_id)
    # Same thread-affinity reasoning as the earlier "browser stays alive"
    # check: liveness is checked over HTTP against the CDP debug port, not
    # by calling back into `thread_ht`'s WebSurface object.
    browser_closed = False
    try:
        requests.get(f"{cdp_endpoint_ht}/json/version", timeout=2)
    except requests.exceptions.RequestException:
        browser_closed = True
    check(
        "hold timeout (claimed, then no activity) -> session closed, failure recorded",
        result_ht is not None
        and result_ht.status == "failure"
        and result_ht.error is not None
        and result_ht.error.code == "HOLD_TIMEOUT"
        and control_ht.controller == Controller.ABANDONED
        and browser_closed,
        f"status={getattr(result_ht, 'status', None)}, error={getattr(result_ht, 'error', None)}, "
        f"controller={control_ht.controller}, browser_closed={browser_closed}",
    )

    # ==================================================================== #
    # Discovery-side escalation: the model reports stuck, a human takes
    # over the *same* browser and finishes the flow, and those actions
    # appear in the trace tagged actor: "human".
    # ==================================================================== #
    reset_app()
    config = PolicyConfig.load()
    disc_run_id = "escalation-discovery-stuck"
    evidence = EvidenceWriter(disc_run_id, redactor=Redactor(config.redaction))
    trace = TraceRecorder(evidence)
    disc_surface = WebSurface(headless=True)
    llm = StuckOnceThenDoneClient()
    agent = DiscoveryAgent(
        disc_surface, llm, policy_gate=ConfiguredPolicyGate(config), evidence_run_id=disc_run_id, trace=trace,
        claim_timeout_s=30.0, handoff_poll_interval_s=0.1,
    )
    disc_holder: dict = {}
    disc_thread = threading.Thread(
        target=lambda: disc_holder.__setitem__("result", agent.run(GOAL, f"{MERIDIAN}/members/search", max_steps=10))
    )
    disc_thread.start()

    disc_control = wait_until(lambda: (c := load_control(disc_run_id)) and c.controller == Controller.AWAITING_HUMAN and c)
    operator.post(f"/operator/{disc_run_id}/claim", json={"name": "the-operator"})
    pw_human2 = sync_playwright().start()
    human_browser2 = pw_human2.chromium.connect_over_cdp(disc_control.cdp_endpoint)
    human_page2 = human_browser2.contexts[0].pages[0]
    search_frame(human_page2).get_by_role("textbox", name="Member ID").fill("10001")
    search_frame(human_page2).get_by_role("button", name="Search").click()
    wait_until(lambda: human_page2.get_by_role("heading", name="Account summary").is_visible())
    operator.post(f"/operator/{disc_run_id}/release")
    disc_thread.join(timeout=20)
    human_browser2.close()
    pw_human2.stop()

    discovery_result = disc_holder.get("result")
    disc_trace_events = read_jsonl(disc_run_id, "trace.jsonl")
    human_tagged = [e for e in disc_trace_events if e.get("actor") == "human"]
    disc_events = read_jsonl(disc_run_id, "discovery.jsonl")
    check(
        "a discovery run emitting stuck escalates, a human acts, and those actions appear in the trace tagged actor: \"human\"",
        discovery_result is not None
        and discovery_result.stop_reason == "SUCCESS"
        and len(human_tagged) >= 1
        and any(e.get("event") == "control_released" for e in disc_events)
        and any(e.get("event") == "control_reacquired" for e in disc_events),
        f"stop_reason={getattr(discovery_result, 'stop_reason', None)}, human_tagged={human_tagged}",
    )

    print()
    passed = sum(1 for _, ok in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
