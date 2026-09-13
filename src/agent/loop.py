"""The discovery agent loop (docs/discovery-spec.md §5). Drives a live
Surface toward a goal, one model-decided action at a time, and stops on one
of a fixed set of conditions. Produces a `DiscoveryResult` carrying the
surviving trace steps the compiler turns into a draft artifact -- nothing
here builds an artifact itself.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from src.agent.llm import LLMClient, LLMProtocolError
from src.agent.risk import classify_risk
from src.agent.trace import TraceRecorder
from src.agent.types import AgentAction, DiscoveryResult, HistoryEntry, StopReason, TraceStep
from src.replay.context import ReplayContext
from src.replay.escalation import InterventionRequest
from src.replay.login import DEFAULT_CREDENTIALS, perform_login
from src.replay.policy import PolicyGate
from src.schema.common import A11yStrategy, DomStrategy, SpatialStrategy, Target
from src.surface.protocol import Surface
from src.surface.serialize import serialize_observation
from src.surface.types import Action, Observation, UINode, WaitSpec

DEFAULT_MAX_STEPS = 15
DEFAULT_TIMEOUT_S = 180.0
_NO_PROGRESS_THRESHOLD = 2  # two consecutive actions that didn't change state
_DENIAL_STREAK_THRESHOLD = 2  # the same action denied twice in a row

_ERROR_HEADING_MARKERS = ("error", "not available", "sign in", "denied")


class DiscoveryAgent:
    def __init__(
        self,
        surface: Surface,
        llm: LLMClient,
        *,
        policy_gate: PolicyGate,
        evidence_run_id: str,
        trace: TraceRecorder,
        entry_allowlist: list[str],
        credentials: tuple[str, str] = DEFAULT_CREDENTIALS,
    ) -> None:
        self._surface = surface
        self._llm = llm
        self._policy_gate = policy_gate
        self._run_id = evidence_run_id
        self._trace = trace
        self._entry_allowlist = entry_allowlist
        self._credentials = credentials

    def run(
        self,
        goal: str,
        entry_point: str,
        *,
        max_steps: int = DEFAULT_MAX_STEPS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        keep_trace: bool = False,
    ) -> DiscoveryResult:
        # Cheap and real, same reasoning as replay's pre-flight allowlist
        # check (docs/replay-spec.md §2.4): navigate's target isn't fixed
        # during discovery, but the entry point is, so it gets the same
        # check before a browser ever opens.
        if not any(entry_point.startswith(prefix) for prefix in self._entry_allowlist):
            self._trace.log_discovery(
                {"event": "preflight_rejected", "reason": "entry point not in allowlist", "entry_point": entry_point}
            )
            return DiscoveryResult(
                run_id=self._run_id, goal=goal, stop_reason="POLICY", success=False,
                trace_steps=[], final_observation=None, entry_point=entry_point, app_version=None,
            )

        self._surface.open(entry_point, self._run_id)

        history: list[HistoryEntry] = []
        trace_steps: list[TraceStep] = []
        start = time.monotonic()
        step_n = 0
        consecutive_unchanged = 0
        denial_streak = 0
        last_denied_signature: tuple | None = None
        stop_reason: StopReason = "MAX_STEPS"
        intervention_path: str | None = None

        # Mechanical, not a model decision -- exactly the same reasoning as
        # ReplayEngine._reauthenticate: the artifact/goal format has no
        # declarative login flow, Surface knows nothing about sessions, and
        # asking the model to guess credentials it was never given is not a
        # discovery problem, it's a missing prerequisite. A fresh browser
        # context has no session, so this always fires once per run.
        observation = self._bootstrap_login_if_needed(entry_point)
        app_version = observation.page.app_version

        while True:
            if time.monotonic() - start > timeout_s:
                stop_reason = "TIMEOUT"
                break
            if step_n >= max_steps:
                stop_reason = "MAX_STEPS"
                break
            if consecutive_unchanged >= _NO_PROGRESS_THRESHOLD:
                stop_reason = "NO_PROGRESS"
                break

            obs_text = serialize_observation(observation)

            try:
                agent_action = self._llm.decide(goal, obs_text, history)
            except LLMProtocolError as exc:
                self._trace.log_trace(
                    {
                        "event": "observation", "step": step_n, "url": observation.page.url,
                        "heading": observation.page.heading, "state_hash": observation.state_hash, "text": obs_text,
                    }
                )
                self._trace.log_trace({"event": "llm_protocol_error", "step": step_n, "error": str(exc)})
                self._trace.log_discovery({"event": "llm_protocol_error", "step": step_n})
                history.append(HistoryEntry(step_n, AgentAction(kind="stuck", reason=str(exc)), "protocol error"))
                stop_reason = "STUCK"
                intervention_path = self._raise_stuck(goal, observation, reason=f"LLM protocol error: {exc}")
                break

            # Resolve the ref (if any) against the PRE-action observation
            # now, before logging anything for this turn -- not later. A
            # value this decision declares sensitive may already be sitting
            # in plain sight on the page (a read target's value existed
            # before the model ever mentioned it), so the observation this
            # turn is about to log can only be redacted correctly if
            # sensitivity is known *before* that log write, not after.
            node: UINode | None = None
            if agent_action.ref is not None:
                node = _find_node(observation, agent_action.ref)

            if agent_action.sensitive:
                if agent_action.kind in ("type", "select") and agent_action.value is not None:
                    self._trace.mark_sensitive(agent_action.value)
                elif agent_action.kind == "read" and node is not None:
                    self._trace.mark_sensitive(node.value if node.value is not None else node.name)

            self._trace.log_trace(
                {
                    "event": "observation", "step": step_n, "url": observation.page.url,
                    "heading": observation.page.heading, "state_hash": observation.state_hash, "text": obs_text,
                }
            )
            self._trace.log_trace({"event": "decision", "step": step_n, "action": _action_to_dict(agent_action)})
            self._trace.log_discovery({"event": "decision", "step": step_n, "kind": agent_action.kind})

            if agent_action.kind == "done":
                if _is_plausible_end_state(observation, entry_point):
                    stop_reason = "SUCCESS"
                    break
                self._trace.log_trace({"event": "done_rejected", "step": step_n})
                history.append(HistoryEntry(step_n, agent_action, "rejected: not a plausible end state yet, keep going"))
                step_n += 1
                continue

            if agent_action.kind == "stuck":
                stop_reason = "STUCK"
                intervention_path = self._raise_stuck(goal, observation, reason=agent_action.reason or "model reported stuck")
                break

            if agent_action.ref is not None and node is None:
                self._trace.log_trace({"event": "invalid_ref", "step": step_n, "ref": agent_action.ref})
                self._trace.log_discovery({"event": "invalid_ref", "step": step_n, "ref": agent_action.ref})
                history.append(
                    HistoryEntry(step_n, agent_action, f"rejected: ref {agent_action.ref!r} is not in the current observation")
                )
                step_n += 1
                continue

            surface_action = self._to_surface_action(agent_action, node)
            ctx = ReplayContext(run_id=self._run_id, attended=True, allow_risky=True, keep_trace=keep_trace)
            decision = self._policy_gate.check(surface_action, ctx)
            signature = (agent_action.kind, agent_action.ref, agent_action.url)

            if not decision.allowed:
                self._trace.log_trace({"event": "policy_denied", "step": step_n, "reason": decision.reason})
                self._trace.log_discovery({"event": "policy_denied", "step": step_n})
                history.append(HistoryEntry(step_n, agent_action, f"denied by policy: {decision.reason or 'not allowed'}"))
                denial_streak = denial_streak + 1 if signature == last_denied_signature else 1
                last_denied_signature = signature
                if denial_streak >= _DENIAL_STREAK_THRESHOLD:
                    stop_reason = "POLICY"
                    break
                step_n += 1
                continue
            denial_streak = 0
            last_denied_signature = None

            before_observation = observation
            action_result = self._surface.act(surface_action)
            after_observation = action_result.observation_after

            self._trace.log_trace(
                {
                    "event": "result", "step": step_n, "ok": action_result.ok,
                    "error_code": action_result.error_code, "state_hash_after": after_observation.state_hash,
                }
            )
            self._trace.log_discovery(
                {"event": "action", "step": step_n, "kind": agent_action.kind, "duration_ms": action_result.duration_ms, "ok": action_result.ok}
            )

            if action_result.ok:
                trace_steps.append(
                    TraceStep(
                        index=step_n, agent_action=agent_action, node=node,
                        before=before_observation, after=after_observation, ok=True,
                        risk=classify_risk(agent_action, node),
                    )
                )
                # docs/discovery-spec.md §8: a screenshot per step, discovery
                # only -- useful for review, not something replay needs.
                screenshot = self._surface.capture_screenshot(mask=None)
                self._trace.save_screenshot(f"step_{step_n:02d}", screenshot)
            history.append(HistoryEntry(step_n, agent_action, "ok" if action_result.ok else f"failed: {action_result.error_code}"))

            consecutive_unchanged = consecutive_unchanged + 1 if after_observation.state_hash == before_observation.state_hash else 0
            observation = after_observation
            step_n += 1

        self._trace.log_discovery({"event": "stop", "reason": stop_reason})
        self._surface.close(keep_trace=keep_trace)

        return DiscoveryResult(
            run_id=self._run_id,
            goal=goal,
            stop_reason=stop_reason,
            success=(stop_reason == "SUCCESS"),
            trace_steps=trace_steps,
            final_observation=observation,
            entry_point=entry_point,
            app_version=app_version,
            intervention_path=intervention_path,
        )

    # --- helpers ----------------------------------------------------------- #

    def _bootstrap_login_if_needed(self, entry_point: str) -> Observation:
        observation = self._surface.observe()
        if "/login" not in observation.page.url:
            return observation

        ctx = ReplayContext(run_id=self._run_id, attended=True, allow_risky=True)
        perform_login(lambda action: self._gated_act(action, ctx), observation.page.url, self._credentials)
        self._trace.log_discovery({"event": "bootstrap_login"})
        result = self._gated_act(Action(kind="navigate", url=entry_point, wait=WaitSpec()), ctx)
        return result.observation_after if result is not None else self._surface.observe()

    def _gated_act(self, action: Action, ctx: ReplayContext):
        # Same choke point as everything else (CLAUDE.md invariant 3): the
        # bootstrap login isn't a model decision, but it's still an action
        # on the live surface, so it still goes through the policy gate.
        decision = self._policy_gate.check(action, ctx)
        if not decision.allowed:
            return None
        return self._surface.act(action)

    def _to_surface_action(self, agent_action: AgentAction, node: UINode | None) -> Action:
        if agent_action.kind == "navigate":
            return Action(kind="navigate", url=agent_action.url, wait=WaitSpec())
        if agent_action.kind == "wait_for":
            if node is not None:
                return Action(kind="wait_for", wait=WaitSpec(strategy="condition", condition=_immediate_bundle(node)))
            return Action(kind="wait_for", wait=WaitSpec(strategy="settle"))
        assert node is not None  # every other kind requires a resolved ref
        bundle = _immediate_bundle(node)
        if agent_action.kind in ("type", "select"):
            return Action(kind=agent_action.kind, target=bundle, value=agent_action.value, wait=WaitSpec())
        return Action(kind=agent_action.kind, target=bundle, wait=WaitSpec())  # click, read

    def _raise_stuck(self, goal: str, observation: Observation, *, reason: str) -> str:
        screenshot = self._surface.capture_screenshot(mask=None)
        screenshot_path = self._trace.save_screenshot("stuck", screenshot)
        request = InterventionRequest(
            run_id=self._run_id,
            capability_id=f"discovery:{goal[:60]}",
            capability_version="draft",
            step_id="(discovery)",
            reason="STUCK",
            page=observation.page,
            screenshot_path=screenshot_path,
            session_held=True,  # discovery is always attended -- there is a human driving this run
            raised_at=datetime.now(timezone.utc),
        )
        self._trace.log_discovery({"event": "escalation", "reason": "STUCK", "detail": reason})
        return self._trace.write_intervention(request)


def _immediate_bundle(node: UINode) -> Target:
    """Not the compiler's ranked, durable bundle (docs/discovery-spec.md
    §6.2) -- a throwaway, highest-confidence-right-now bundle so act()
    hits exactly the node the model cited. Precision matters more than
    long-term stability here: this bundle is used once and discarded.
    """
    strategies: list[Any] = []
    if node.dom_hint:
        strategies.append(DomStrategy(css=node.dom_hint))
    if node.name:
        strategies.append(A11yStrategy(role=node.role, name=node.name))
    if not strategies and node.nearby_text:
        strategies.append(SpatialStrategy(anchor_text=node.nearby_text[0], direction="right"))
    if not strategies:
        raise ValueError(f"node {node.ref!r} has no usable strategy for immediate action")
    return Target(description=f"agent ref {node.ref} ({node.role} {node.name!r})", strategies=strategies)


def _find_node(observation: Observation, ref: str) -> UINode | None:
    return next((n for n in observation.nodes if n.ref == ref), None)


def _is_plausible_end_state(observation: Observation, entry_point: str) -> bool:
    if observation.page.heading is None:
        return False
    if observation.page.url.rstrip("/") == entry_point.rstrip("/"):
        return False
    lowered = observation.page.heading.lower()
    if any(marker in lowered for marker in _ERROR_HEADING_MARKERS):
        return False
    return True


def _action_to_dict(action: AgentAction) -> dict[str, Any]:
    return asdict(action)
