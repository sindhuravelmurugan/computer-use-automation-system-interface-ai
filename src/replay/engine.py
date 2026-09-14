"""The replay engine (docs/replay-spec.md). Deterministic execution of a
capability artifact with no LLM in the decision loop: resolve, act, observe,
then recoveries -> outcomes -> checkpoint -> default deny, per step.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Literal

from src.escalation.control import DEFAULT_CLAIM_TIMEOUT_S, DEFAULT_HOLD_TIMEOUT_S, SessionControl
from src.escalation.handoff import DEFAULT_POLL_INTERVAL_S, human_action_count, wait_for_handoff
from src.policy import ConfiguredPolicyGate, PolicyConfig, PolicyDecision, PolicyGate, Redactor
from src.replay.context import ReplayContext
from src.replay.detectors import describe_detector, describe_observed_state, evaluate_detector, in_scope
from src.replay.escalation import EscalationReason, raise_intervention
from src.replay.evidence import EvidenceWriter
from src.replay.handback import verify_handback
from src.replay.ledger import AttemptLedger
from src.replay.login import DEFAULT_CREDENTIALS, perform_login
from src.replay.overrides import load_override_file
from src.replay.preflight import OverrideLoader, run_preflight
from src.replay.templating import substitute, substitute_detector
from src.replay.transforms import TransformError, apply_transform
from src.schema.artifact import CapabilityArtifact
from src.schema.recoveries import Recovery, RecoveryAction
from src.schema.result import ErrorDetail, OutcomeResult, RecoveryApplied, ReplayResult
from src.schema.steps import Step
from src.surface.protocol import Surface
from src.surface.types import Action, Observation, Rect, WaitSpec

_DEFAULT_WAIT_MS = 10_000


class ReplayEngine:
    def __init__(
        self,
        surface_factory: Callable[[], Surface],
        *,
        policy_gate: PolicyGate | None = None,
        redaction_config=None,
        override_loader: OverrideLoader | None = None,
        credentials: tuple[str, str] = DEFAULT_CREDENTIALS,
        evidence_dir: str = "evidence",
        claim_timeout_s: float = DEFAULT_CLAIM_TIMEOUT_S,
        hold_timeout_s: float = DEFAULT_HOLD_TIMEOUT_S,
        handoff_poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        self._surface_factory = surface_factory
        # One config load covers both: the gate and the redactor agree on
        # what "sensitive-looking" means because they're built from the
        # same file, unless a caller deliberately overrides one.
        if policy_gate is None or redaction_config is None:
            loaded_config = PolicyConfig.load()
        self._policy_gate = policy_gate if policy_gate is not None else ConfiguredPolicyGate(loaded_config)
        self._redaction_config = redaction_config if redaction_config is not None else loaded_config.redaction
        self._override_loader = override_loader or load_override_file
        self._credentials = credentials
        self._evidence_dir = evidence_dir
        # docs/escalation-spec.md §1's two timeouts, and how often
        # _escalate_and_wait polls control.json while blocked on a human.
        # Configurable (rather than hardcoded) so a test/acceptance run
        # doesn't have to sit through the real 5/10-minute defaults to
        # exercise claim_timeout/hold_timeout.
        self._claim_timeout_s = claim_timeout_s
        self._hold_timeout_s = hold_timeout_s
        self._handoff_poll_interval_s = handoff_poll_interval_s
        # Exposed for evidence/test cleanup when a run holds the session
        # open (escalation, session_held=True). Never used by the engine
        # itself after run() returns.
        self.last_surface: Surface | None = None

    def run(self, artifact: CapabilityArtifact, inputs: dict[str, Any], context: ReplayContext) -> ReplayResult:
        start = time.monotonic()
        redactor = Redactor(self._redaction_config)
        evidence = EvidenceWriter(context.run_id, base_dir=self._evidence_dir, redactor=redactor)

        # Mark declared-sensitive input values before anything else touches
        # evidence -- including a pre-flight INVALID_INPUT error, whose
        # `observed` field would otherwise echo the raw (possibly
        # malformed but still sensitive) supplied value.
        for param in artifact.inputs:
            if param.sensitive and param.name in inputs:
                evidence.mark_sensitive(inputs[param.name])

        merged_artifact, resolved_inputs, error = run_preflight(
            artifact, inputs, context, self._policy_gate, self._override_loader
        )
        if error is not None:
            evidence.log({"event": "preflight_rejected", "code": error.code})
            return self._preflight_failure(evidence, artifact, context, error, start)

        assert merged_artifact is not None and resolved_inputs is not None
        artifact = merged_artifact

        sensitive_output_names = {o.name for o in artifact.outputs if o.sensitive}

        surface = self._surface_factory()
        self.last_surface = surface
        surface.open(artifact.surface.entry_point, context.run_id)

        return self._run_steps(surface, artifact, resolved_inputs, context, evidence, sensitive_output_names, start)

    # --- the per-step loop (docs/replay-spec.md §4) ---------------------- #

    def _run_steps(
        self,
        surface: Surface,
        artifact: CapabilityArtifact,
        resolved_inputs: dict[str, Any],
        context: ReplayContext,
        evidence: EvidenceWriter,
        sensitive_output_names: set[str],
        start: float,
    ) -> ReplayResult:
        ledger = AttemptLedger()
        recoveries_applied: list[RecoveryApplied] = []
        # Accumulates across every escalate-claim-release cycle in this run
        # (docs/escalation-spec.md §6's "handoffs" array), regardless of how
        # many separate steps end up escalating or how the run ultimately
        # ends -- so every terminal ReplayResult below threads it through.
        handoffs: list[dict[str, Any]] = []
        outputs_extracted: dict[str, Any] = {}
        sensitive_output_bounds: list[Rect] = []
        step_index_by_id = {s.id: i for i, s in enumerate(artifact.steps)}
        completed = 0
        last_observation: Observation | None = None
        version_checked = False
        mutated_state = False

        i = 0
        while i < len(artifact.steps):
            step = artifact.steps[i]

            # 4.1 Resolve. Recorded on every step -- this is the drift
            # signal. Note act() (for click/type/select) resolves *again*
            # internally, on purpose: it never trusts a handle computed
            # outside itself (see the frame-caching fix in src/surface/web.py).
            # This call exists for the engine's own log + fail-fast gate.
            resolution = None
            if step.target is not None:
                resolution = surface.resolve(step.target)
                evidence.log(
                    {
                        "event": "resolve",
                        "step_id": step.id,
                        "strategy_index": resolution.strategy_index,
                        "status": resolution.status,
                    }
                )
                if resolution.status in ("not_found", "ambiguous"):
                    code = "TARGET_NOT_FOUND" if resolution.status == "not_found" else "TARGET_AMBIGUOUS"
                    return self._hard_failure(
                        evidence, artifact, context, surface, step.id, code,
                        expected=f"target resolves: {step.target.description}",
                        observed=resolution.status,
                        start=start, completed=completed,
                        sensitive_output_names=sensitive_output_names,
                        recoveries_applied=recoveries_applied,
                        handoffs=handoffs,
                        strategy_used=resolution.strategy_index,
                    )

            # 4.2 Act
            action_value = substitute(step.value, resolved_inputs) if step.value else step.value
            wait = WaitSpec(
                strategy=step.wait.strategy if step.wait else "settle",  # type: ignore[arg-type]
                timeout_ms=step.wait.timeout_ms if step.wait else _DEFAULT_WAIT_MS,
            )
            action = Action(
                kind=step.action,
                target=step.target,
                value=action_value,
                url=action_value if step.action == "navigate" else None,
                wait=wait,
                # Authoritative: a human reviewed and declared this at
                # compile time (docs/policy-spec.md §3 "on replay: the
                # artifact is authoritative"). The gate reads it instead of
                # guessing from the control name.
                risk=step.risk,
            )

            action_result, decision = self._gated_act(surface, action, context)
            if action_result is None:
                evidence.log(
                    {"event": "policy_denied", "step_id": step.id, "verdict": decision.verdict, "rule": decision.rule, "reason": decision.reason}
                )
                if decision.verdict == "requires_approval":
                    escalation_result = self._escalate_and_wait(
                        surface=surface, artifact=artifact, context=context, evidence=evidence, step=step,
                        reason="POLICY_REQUIRES_APPROVAL", mutated_state=mutated_state,
                        sensitive_output_bounds=sensitive_output_bounds, resolved_inputs=resolved_inputs,
                        recoveries_applied=recoveries_applied, handoffs=handoffs, outputs_extracted=outputs_extracted,
                        start=start, completed=completed, sensitive_output_names=sensitive_output_names,
                    )
                    if isinstance(escalation_result, ReplayResult):
                        return escalation_result
                    _, post_handback_observation = escalation_result
                    outcome_result = self._check_outcome(
                        artifact, step, post_handback_observation, recoveries_applied, evidence, context,
                        surface, start, completed, sensitive_output_names, handoffs,
                    )
                    if outcome_result is not None:
                        return outcome_result
                    self._extract_outputs_for_step(
                        artifact, step, None, surface, outputs_extracted, sensitive_output_bounds, evidence
                    )
                    completed += 1
                    i += 1
                    continue
                return self._hard_failure(
                    evidence, artifact, context, surface, step.id, "POLICY_DENIED",
                    expected="policy gate allows this action",
                    observed=f"{decision.verdict} by rule {decision.rule!r}: {decision.reason}",
                    start=start, completed=completed,
                    sensitive_output_names=sensitive_output_names,
                    recoveries_applied=recoveries_applied,
                    handoffs=handoffs,
                )
            evidence.log(
                {"event": "action", "step_id": step.id, "kind": step.action, "duration_ms": action_result.duration_ms}
            )
            observation = action_result.observation_after
            last_observation = observation

            if not version_checked:
                version_checked = True
                self._check_version_drift(artifact, observation, evidence)

            if step.risk == "risky":
                mutated_state = True

            # 4.3 Recovery detectors -- first. A modal overlaying a
            # not-found page must be dismissed before deciding what the
            # page says.
            recovery_directive = self._check_recoveries(
                artifact, step, observation, ledger, surface, resolved_inputs,
                recoveries_applied, evidence, context,
            )
            if recovery_directive is not None:
                kind = recovery_directive[0]
                if kind == "escalate":
                    reason: EscalationReason = recovery_directive[1]
                    escalation_result = self._escalate_and_wait(
                        surface=surface, artifact=artifact, context=context, evidence=evidence, step=step,
                        reason=reason, mutated_state=mutated_state,
                        sensitive_output_bounds=sensitive_output_bounds, resolved_inputs=resolved_inputs,
                        recoveries_applied=recoveries_applied, handoffs=handoffs, outputs_extracted=outputs_extracted,
                        start=start, completed=completed, sensitive_output_names=sensitive_output_names,
                    )
                    if isinstance(escalation_result, ReplayResult):
                        return escalation_result
                    _, post_handback_observation = escalation_result
                    outcome_result = self._check_outcome(
                        artifact, step, post_handback_observation, recoveries_applied, evidence, context,
                        surface, start, completed, sensitive_output_names, handoffs,
                    )
                    if outcome_result is not None:
                        return outcome_result
                    self._extract_outputs_for_step(
                        artifact, step, None, surface, outputs_extracted, sensitive_output_bounds, evidence
                    )
                    completed += 1
                    i += 1
                    continue
                if kind == "retry_step":
                    continue  # same i
                if kind == "continue":
                    i += 1
                    continue
                if kind == "restart_from":
                    i = step_index_by_id[recovery_directive[1]]
                    continue

            # 4.4 Outcome detectors -- second. A success path; nothing here
            # is logged as an error.
            outcome_result = self._check_outcome(
                artifact, step, observation, recoveries_applied, evidence, context,
                surface, start, completed, sensitive_output_names, handoffs,
            )
            if outcome_result is not None:
                return outcome_result

            # Output extraction is bound to a specific step's target, so it
            # happens the moment that step completes rather than being
            # deferred to the end (by then the page may have moved on).
            self._extract_outputs_for_step(
                artifact, step, resolution, surface, outputs_extracted, sensitive_output_bounds, evidence
            )

            # 4.5 Checkpoint -- third. 4.6 Default deny -- fourth: a step
            # with no declared checkpoint has nothing to fail here, so it
            # only denies when the *action itself* came back mechanically
            # unhealthy (timeout / not interactable) with nothing above
            # having explained it.
            if step.checkpoint is not None:
                checkpoint = substitute_detector(step.checkpoint, resolved_inputs)
                passed = evaluate_detector(checkpoint, observation)
                evidence.log({"event": "checkpoint", "step_id": step.id, "result": "pass" if passed else "fail"})
                if not passed:
                    return self._hard_failure(
                        evidence, artifact, context, surface, step.id, "CHECKPOINT_FAILED",
                        expected=describe_detector(checkpoint),
                        observed=describe_observed_state(observation),
                        start=start, completed=completed,
                        sensitive_output_names=sensitive_output_names,
                        recoveries_applied=recoveries_applied,
                        handoffs=handoffs,
                    )
            elif not action_result.ok:
                return self._hard_failure(
                    evidence, artifact, context, surface, step.id, f"ACTION_FAILED_{action_result.error_code}",
                    expected="action completes without a surface-level error",
                    observed=str(action_result.error_code),
                    start=start, completed=completed,
                    sensitive_output_names=sensitive_output_names,
                    recoveries_applied=recoveries_applied,
                    handoffs=handoffs,
                )

            completed += 1
            i += 1

        # Reaching the last step is not success -- verify the declared end
        # state and that every required output actually extracted.
        assert last_observation is not None
        success_ok = evaluate_detector(artifact.success.checkpoint, last_observation)
        evidence.log({"event": "success_checkpoint", "result": "pass" if success_ok else "fail"})
        if not success_ok:
            return self._hard_failure(
                evidence, artifact, context, surface, artifact.steps[-1].id, "SUCCESS_CHECKPOINT_FAILED",
                expected=describe_detector(artifact.success.checkpoint),
                observed=describe_observed_state(last_observation),
                start=start, completed=completed,
                sensitive_output_names=sensitive_output_names,
                recoveries_applied=recoveries_applied,
                handoffs=handoffs,
            )

        if artifact.success.require_all_outputs:
            missing = [o.name for o in artifact.outputs if o.required and o.name not in outputs_extracted]
            if missing:
                return self._hard_failure(
                    evidence, artifact, context, surface, artifact.steps[-1].id, "OUTPUT_EXTRACTION_FAILED",
                    expected=f"required outputs extracted: {missing!r}",
                    observed="one or more required outputs did not resolve",
                    start=start, completed=completed,
                    sensitive_output_names=sensitive_output_names,
                    recoveries_applied=recoveries_applied,
                    handoffs=handoffs,
                )

        screenshot = surface.capture_screenshot(mask=sensitive_output_bounds or None)
        evidence.save_screenshot("final_success", screenshot)

        result = ReplayResult(
            status="success",
            capability_id=artifact.capability.id,
            capability_version=artifact.capability.version,
            run_id=context.run_id,
            outputs=outputs_extracted,
            recoveries_applied=recoveries_applied,
            handoffs=handoffs,
            duration_ms=self._elapsed_ms(start),
            steps_completed=completed,
        )
        evidence.write_result(result, sensitive_output_names)
        surface.close(keep_trace=context.keep_trace)
        return result

    # --- escalation & handoff (docs/escalation-spec.md) --------------------- #

    def _check_outcome(
        self,
        artifact: CapabilityArtifact,
        step: Step,
        observation: Observation,
        recoveries_applied: list[RecoveryApplied],
        evidence: EvidenceWriter,
        context: ReplayContext,
        surface: Surface,
        start: float,
        completed: int,
        sensitive_output_names: set[str],
        handoffs: list[dict[str, Any]],
    ) -> ReplayResult | None:
        """The business-outcome half of 4.4 (docs/replay-spec.md §4), pulled
        out so the post-handback continuation (§5's "step checkpoint met")
        can run the exact same check the normal per-step loop does, against
        whatever observation reacquiring produced.
        """
        for outcome in artifact.outcomes:
            if not in_scope(outcome.check_after, step.id):
                continue
            if evaluate_detector(outcome.detect, observation):
                evidence.log({"event": "outcome", "step_id": step.id, "code": outcome.code})
                result = ReplayResult(
                    status="business_outcome",
                    capability_id=artifact.capability.id,
                    capability_version=artifact.capability.version,
                    run_id=context.run_id,
                    outcome=OutcomeResult(code=outcome.code, returns=outcome.returns),
                    recoveries_applied=recoveries_applied,
                    handoffs=handoffs,
                    duration_ms=self._elapsed_ms(start),
                    steps_completed=completed,
                )
                evidence.write_result(result, sensitive_output_names)
                surface.close(keep_trace=context.keep_trace)
                return result
        return None

    def _escalate_and_wait(
        self,
        *,
        surface: Surface,
        artifact: CapabilityArtifact,
        context: ReplayContext,
        evidence: EvidenceWriter,
        step: Step,
        reason: EscalationReason,
        mutated_state: bool,
        sensitive_output_bounds: list[Rect],
        resolved_inputs: dict[str, Any],
        outputs_extracted: dict[str, Any],
        recoveries_applied: list[RecoveryApplied],
        handoffs: list[dict[str, Any]],
        start: float,
        completed: int,
        sensitive_output_names: set[str],
    ) -> ReplayResult | tuple[Literal["continue"], Observation]:
        """The three triggers (docs/escalation-spec.md §2) all land here.
        Raises the InterventionRequest first (unconditional, per docs/
        replay-spec.md §6); whether the session is held decides whether
        that's the end of it (an unattended, read-only run just fails) or
        the start of a real handoff: release control, block on
        ``control.json`` until a human claims and releases it back, then
        run the §5 verification sequence -- re-escalating, up to a small
        budget, rather than ever guessing how far the human got.
        """
        intervention = raise_intervention(
            surface=surface, context=context, evidence=evidence,
            capability_id=artifact.capability.id, capability_version=artifact.capability.version,
            step_id=step.id, reason=reason, mutated_state=mutated_state,
            sensitive_bounds=sensitive_output_bounds or None,
        )
        evidence.log(
            {"event": "escalation", "step_id": step.id, "reason": reason, "session_held": intervention.session_held}
        )

        if not intervention.session_held:
            return self._escalated_failure(
                evidence, artifact, context, reason, step.id, recoveries_applied, start, completed,
                sensitive_output_names, handoffs,
            )

        current_reason: str = reason
        re_escalation_budget = 3
        cycle = 0
        while True:
            cycle += 1
            handle = surface.release()
            control = SessionControl.escalate(
                run_id=context.run_id,
                capability_id=artifact.capability.id,
                capability_version=artifact.capability.version,
                step_id=step.id,
                reason=current_reason,
                cdp_endpoint=handle.cdp_endpoint,
                page_url=handle.page_url,
                screenshot_path=intervention.screenshot_path,
                claim_timeout_s=self._claim_timeout_s,
                hold_timeout_s=self._hold_timeout_s,
            )
            control.handoffs = list(handoffs)
            control.save(self._evidence_dir)
            evidence.log({"event": "control_released", "to": "human", "step_id": step.id, "reason": current_reason})

            wait = wait_for_handoff(
                context.run_id, evidence_dir=self._evidence_dir, poll_interval_s=self._handoff_poll_interval_s,
                on_tick=surface.pump_events,
            )
            if wait.outcome != "returned":
                surface.close(keep_trace=context.keep_trace)
                code = "CLAIM_TIMEOUT" if wait.outcome == "claim_timeout" else "HOLD_TIMEOUT"
                return self._escalated_failure(
                    evidence, artifact, context, code, step.id, recoveries_applied, start, completed,
                    sensitive_output_names, wait.control.handoffs,
                )

            surface.reacquire(handle)
            control = wait.control
            control.mark_reacquired()
            control.save(self._evidence_dir)

            observation = surface.observe()
            screenshot = surface.capture_screenshot(mask=sensitive_output_bounds or None)
            shot_path = evidence.save_screenshot(f"reacquire_{step.id}_{cycle}", screenshot)
            evidence.log({"event": "control_reacquired", "step_id": step.id, "screenshot": shot_path})

            verdict = verify_handback(artifact, step, resolved_inputs, observation)
            evidence.log({"event": "handback", "step_id": step.id, "verdict": verdict})

            if verdict == "capability_success":
                control.mark_verified()
                control.save(self._evidence_dir)
                self._extract_all_outputs(artifact, surface, outputs_extracted, sensitive_output_bounds, evidence)
                result = ReplayResult(
                    status="success",
                    capability_id=artifact.capability.id,
                    capability_version=artifact.capability.version,
                    run_id=context.run_id,
                    outputs=outputs_extracted,
                    recoveries_applied=recoveries_applied,
                    handoffs=control.handoffs,
                    human_intervention=True,
                    duration_ms=self._elapsed_ms(start),
                    steps_completed=completed,
                )
                evidence.write_result(result, sensitive_output_names)
                surface.close(keep_trace=context.keep_trace)
                return result

            if verdict == "step_checkpoint_met":
                control.mark_verified()
                control.save(self._evidence_dir)
                handoffs.extend(control.handoffs[len(handoffs):])
                return "continue", observation

            # unrecognized -- re-escalate rather than blindly continuing
            # (docs/escalation-spec.md §5's "human leaves the app in an
            # unrelated state").
            re_escalation_budget -= 1
            if re_escalation_budget <= 0:
                surface.close(keep_trace=context.keep_trace)
                return self._escalated_failure(
                    evidence, artifact, context, "HANDBACK_VERIFICATION_EXHAUSTED", step.id,
                    recoveries_applied, start, completed, sensitive_output_names, control.handoffs,
                )
            handoffs.extend(control.handoffs[len(handoffs):])
            current_reason = "HANDBACK_UNVERIFIED"
            control.re_escalate(current_reason)
            control.save(self._evidence_dir)

    def _extract_all_outputs(
        self,
        artifact: CapabilityArtifact,
        surface: Surface,
        outputs_extracted: dict[str, Any],
        sensitive_output_bounds: list[Rect],
        evidence: EvidenceWriter,
    ) -> None:
        """Used only on the "human completed the flow manually" handback
        path (docs/escalation-spec.md §5): every declared output gets
        resolved fresh against wherever the human left the page, not just
        the ones tied to whatever step happened to be escalating.
        """
        for output in artifact.outputs:
            out_res = surface.resolve(output.source.target)
            if out_res.status != "resolved":
                continue
            assert out_res.node is not None
            raw = out_res.node.value if out_res.node.value is not None else out_res.node.name
            if output.sensitive:
                evidence.mark_sensitive(raw)
            try:
                value = apply_transform(output.source.transform, raw)
            except TransformError:
                continue
            outputs_extracted[output.name] = value
            if output.sensitive:
                evidence.mark_sensitive(value)
                sensitive_output_bounds.append(out_res.node.bounds)

    # --- helpers ----------------------------------------------------------- #

    def _check_recoveries(
        self,
        artifact: CapabilityArtifact,
        step,
        observation: Observation,
        ledger: AttemptLedger,
        surface: Surface,
        resolved_inputs: dict[str, Any],
        recoveries_applied: list[RecoveryApplied],
        evidence: EvidenceWriter,
        context: ReplayContext,
    ) -> tuple[str, str] | tuple[str] | None:
        """Returns a directive: ("retry_step",), ("continue",),
        ("restart_from", step_id), or ("escalate", reason). None means no
        recovery matched -- proceed to outcomes/checkpoint.
        """
        matched: Recovery | None = None
        for recovery in artifact.recoveries:
            if not in_scope(recovery.check_after, step.id):
                continue
            if evaluate_detector(recovery.detect, observation):
                matched = recovery
                break

        if matched is None:
            return None

        attempts = ledger.increment(matched.code, step.id)
        if attempts > matched.max_attempts:
            # §5: the condition is promoted; it never loops.
            return ("escalate", "EXHAUSTED_RECOVERY")

        evidence.log({"event": "recovery", "code": matched.code, "step_id": step.id, "attempt": attempts})
        self._apply_recovery_action(surface, matched.recovery, resolved_inputs, context, evidence, step.id)
        recoveries_applied.append(RecoveryApplied(code=matched.code, step_id=step.id, attempts=attempts))

        if matched.then == "escalate":
            return ("escalate", "ESCALATE_DIRECTIVE")
        if matched.then == "restart_from":
            assert matched.restart_step_id is not None
            return ("restart_from", matched.restart_step_id)
        if matched.then == "retry_step":
            return ("retry_step",)
        return ("continue",)

    def _gated_act(self, surface: Surface, action: Action, context: ReplayContext) -> tuple[Any, PolicyDecision]:
        """The one choke point (CLAUDE.md invariant 3): every action from
        the replay path -- a step's own action, and every action a recovery
        takes on its behalf, including re_authenticate's sub-actions --
        passes through here, never surface.act() directly. Always returns
        the PolicyDecision alongside the (possibly None) ActionResult, so a
        caller that cares can tell a denial from a requires_approval
        without a second gate.check() call.
        """
        decision = self._policy_gate.check(action, context)
        if decision.verdict != "allow":
            return None, decision
        return surface.act(action), decision

    def _apply_recovery_action(
        self,
        surface: Surface,
        recovery_action: RecoveryAction,
        resolved_inputs: dict[str, Any],
        context: ReplayContext,
        evidence: EvidenceWriter,
        step_id: str,
    ) -> None:
        if recovery_action.action == "re_authenticate":
            self._reauthenticate(surface, context)
            return
        value = substitute(recovery_action.value, resolved_inputs) if recovery_action.value else recovery_action.value
        _, decision = self._gated_act(
            surface,
            Action(
                kind=recovery_action.action,  # type: ignore[arg-type]
                target=recovery_action.target,
                value=value,
                url=value if recovery_action.action == "navigate" else None,
                wait=WaitSpec(),
            ),
            context,
        )
        if decision.verdict != "allow":
            # A recovery's own action is gated exactly like any other
            # (CLAUDE.md invariant 3, docs/policy-spec.md acceptance check
            # "recovery actions pass through the gate"). If it's denied,
            # the recovery silently did nothing -- worth its own log line
            # rather than only being inferable from whatever happens next.
            evidence.log(
                {"event": "policy_denied", "step_id": step_id, "verdict": decision.verdict, "rule": decision.rule, "reason": decision.reason}
            )

    def _reauthenticate(self, surface: Surface, context: ReplayContext) -> None:
        """"re_authenticate" has to be handled here, not delegated: the
        artifact format has no declarative login flow, and Surface
        deliberately knows nothing about credentials or sessions. The
        actual sequence lives in src.replay.login, shared with discovery's
        bootstrap and the probe pass.
        """
        current_url = surface.observe().page.url
        perform_login(lambda action: self._gated_act(surface, action, context), current_url, self._credentials)

    def _extract_outputs_for_step(
        self,
        artifact: CapabilityArtifact,
        step,
        resolution,
        surface: Surface,
        outputs_extracted: dict[str, Any],
        sensitive_output_bounds: list[Rect],
        evidence: EvidenceWriter,
    ) -> None:
        for output in artifact.outputs:
            if output.source.step_id != step.id:
                continue
            # Re-use the step's own resolve() result when the output reads
            # the same target (the common case, e.g. a "read" step) instead
            # of re-resolving.
            if resolution is not None and step.target is not None and output.source.target == step.target:
                out_res = resolution
            else:
                out_res = surface.resolve(output.source.target)
            if out_res.status != "resolved":
                continue  # required-ness is enforced later, not here
            assert out_res.node is not None
            raw = out_res.node.value if out_res.node.value is not None else out_res.node.name
            if output.sensitive:
                # Mark both the raw, pre-transform text (what an
                # observation/checkpoint elsewhere might quote verbatim,
                # e.g. "4,832.10") and the transformed value (e.g.
                # Decimal("4832.10")) -- the two don't stringify the same,
                # and either can end up in a log or an error message.
                evidence.mark_sensitive(raw)
            try:
                value = apply_transform(output.source.transform, raw)
            except TransformError:
                continue
            outputs_extracted[output.name] = value
            if output.sensitive:
                evidence.mark_sensitive(value)
                sensitive_output_bounds.append(out_res.node.bounds)

    def _check_version_drift(self, artifact: CapabilityArtifact, observation: Observation, evidence: EvidenceWriter) -> None:
        recorded = artifact.provenance.recorded_against
        observed = observation.page.app_version
        if recorded and observed and recorded != observed:
            evidence.log({"event": "version_drift", "recorded_against": recorded, "observed": observed})

    def _elapsed_ms(self, start: float) -> int:
        return int((time.monotonic() - start) * 1000)

    def _preflight_failure(
        self, evidence: EvidenceWriter, artifact: CapabilityArtifact, context: ReplayContext, error: ErrorDetail, start: float
    ) -> ReplayResult:
        result = ReplayResult(
            status="failure",
            capability_id=artifact.capability.id,
            capability_version=artifact.capability.version,
            run_id=context.run_id,
            error=error,
            recoveries_applied=[],
            duration_ms=self._elapsed_ms(start),
            steps_completed=0,
        )
        evidence.write_result(result, set())
        return result

    def _hard_failure(
        self,
        evidence: EvidenceWriter,
        artifact: CapabilityArtifact,
        context: ReplayContext,
        surface: Surface,
        step_id: str,
        code: str,
        *,
        expected: str,
        observed: str,
        start: float,
        completed: int,
        sensitive_output_names: set[str],
        recoveries_applied: list[RecoveryApplied],
        handoffs: list[dict[str, Any]] | None = None,
        strategy_used: int | None = None,
    ) -> ReplayResult:
        screenshot = surface.capture_screenshot(mask=None)
        screenshot_path = evidence.save_screenshot(f"failure_{step_id}", screenshot)
        evidence.log({"event": "failure", "step_id": step_id, "code": code})

        error = ErrorDetail(
            code=code,
            step_id=step_id,
            expected=expected,
            observed=observed,
            strategy_used=str(strategy_used) if strategy_used is not None else None,
            evidence=screenshot_path,
        )
        result = ReplayResult(
            status="failure",
            capability_id=artifact.capability.id,
            capability_version=artifact.capability.version,
            run_id=context.run_id,
            error=error,
            recoveries_applied=recoveries_applied,
            handoffs=handoffs or [],
            duration_ms=self._elapsed_ms(start),
            steps_completed=completed,
        )
        evidence.write_result(result, sensitive_output_names)
        surface.close(keep_trace=context.keep_trace)
        return result

    def _escalated_failure(
        self,
        evidence: EvidenceWriter,
        artifact: CapabilityArtifact,
        context: ReplayContext,
        reason: str,
        step_id: str,
        recoveries_applied: list[RecoveryApplied],
        start: float,
        completed: int,
        sensitive_output_names: set[str],
        handoffs: list[dict[str, Any]] | None = None,
    ) -> ReplayResult:
        # The caller (raise_intervention, or _escalate_and_wait's timeout /
        # exhausted-re-escalation branches) already closed the surface
        # whenever the session wasn't left open for a human -- nothing left
        # to do here but build the result.
        error = ErrorDetail(
            code=reason,
            step_id=step_id,
            expected="a declared recovery resolves the condition",
            observed=f"escalated: {reason}",
        )
        result = ReplayResult(
            status="failure",
            capability_id=artifact.capability.id,
            capability_version=artifact.capability.version,
            run_id=context.run_id,
            error=error,
            recoveries_applied=recoveries_applied,
            handoffs=handoffs or [],
            duration_ms=self._elapsed_ms(start),
            steps_completed=completed,
        )
        evidence.write_result(result, sensitive_output_names)
        return result
