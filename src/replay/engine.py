"""The replay engine (docs/replay-spec.md). Deterministic execution of a
capability artifact with no LLM in the decision loop: resolve, act, observe,
then recoveries -> outcomes -> checkpoint -> default deny, per step.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from src.replay.context import ReplayContext
from src.replay.detectors import describe_detector, describe_observed_state, evaluate_detector, in_scope
from src.replay.escalation import EscalationReason, raise_intervention
from src.replay.evidence import EvidenceWriter
from src.replay.ledger import AttemptLedger
from src.replay.login import DEFAULT_CREDENTIALS, perform_login
from src.replay.overrides import load_override_file
from src.replay.policy import AllowAllGate, PolicyGate
from src.replay.preflight import OverrideLoader, run_preflight
from src.replay.templating import substitute, substitute_detector
from src.replay.transforms import TransformError, apply_transform
from src.schema.artifact import CapabilityArtifact
from src.schema.recoveries import Recovery, RecoveryAction
from src.schema.result import ErrorDetail, OutcomeResult, RecoveryApplied, ReplayResult
from src.surface.protocol import Surface
from src.surface.types import Action, Observation, Rect, WaitSpec

DEFAULT_ALLOWLIST = ["http://127.0.0.1:5001", "http://127.0.0.1:5002"]

_DEFAULT_WAIT_MS = 10_000


class ReplayEngine:
    def __init__(
        self,
        surface_factory: Callable[[], Surface],
        *,
        allowlist: list[str] | None = None,
        policy_gate: PolicyGate | None = None,
        override_loader: OverrideLoader | None = None,
        credentials: tuple[str, str] = DEFAULT_CREDENTIALS,
        evidence_dir: str = "evidence",
    ) -> None:
        self._surface_factory = surface_factory
        self._allowlist = allowlist if allowlist is not None else list(DEFAULT_ALLOWLIST)
        self._policy_gate = policy_gate or AllowAllGate()
        self._override_loader = override_loader or load_override_file
        self._credentials = credentials
        self._evidence_dir = evidence_dir
        # Exposed for evidence/test cleanup when a run holds the session
        # open (escalation, session_held=True). Real takeover is
        # Surface.reacquire() -- step 7. Never used by the engine itself
        # after run() returns.
        self.last_surface: Surface | None = None

    def run(self, artifact: CapabilityArtifact, inputs: dict[str, Any], context: ReplayContext) -> ReplayResult:
        start = time.monotonic()
        evidence = EvidenceWriter(context.run_id, base_dir=self._evidence_dir)

        merged_artifact, resolved_inputs, error = run_preflight(
            artifact, inputs, context, self._allowlist, self._override_loader
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
            )

            action_result = self._gated_act(surface, action, context)
            if action_result is None:
                return self._hard_failure(
                    evidence, artifact, context, surface, step.id, "POLICY_DENIED",
                    expected="policy gate allows this action",
                    observed="denied",
                    start=start, completed=completed,
                    sensitive_output_names=sensitive_output_names,
                    recoveries_applied=recoveries_applied,
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
                    intervention = raise_intervention(
                        surface=surface, context=context, evidence=evidence,
                        capability_id=artifact.capability.id,
                        capability_version=artifact.capability.version,
                        step_id=step.id, reason=reason, mutated_state=mutated_state,
                        sensitive_bounds=sensitive_output_bounds or None,
                    )
                    evidence.log(
                        {"event": "escalation", "step_id": step.id, "reason": reason, "session_held": intervention.session_held}
                    )
                    return self._escalated_failure(
                        evidence, artifact, context, reason, step.id, recoveries_applied, start, completed,
                        sensitive_output_names,
                    )
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
                        duration_ms=self._elapsed_ms(start),
                        steps_completed=completed,
                    )
                    evidence.write_result(result, sensitive_output_names)
                    surface.close(keep_trace=context.keep_trace)
                    return result

            # Output extraction is bound to a specific step's target, so it
            # happens the moment that step completes rather than being
            # deferred to the end (by then the page may have moved on).
            self._extract_outputs_for_step(
                artifact, step, resolution, surface, outputs_extracted, sensitive_output_bounds
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
                    )
            elif not action_result.ok:
                return self._hard_failure(
                    evidence, artifact, context, surface, step.id, f"ACTION_FAILED_{action_result.error_code}",
                    expected="action completes without a surface-level error",
                    observed=str(action_result.error_code),
                    start=start, completed=completed,
                    sensitive_output_names=sensitive_output_names,
                    recoveries_applied=recoveries_applied,
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
            duration_ms=self._elapsed_ms(start),
            steps_completed=completed,
        )
        evidence.write_result(result, sensitive_output_names)
        surface.close(keep_trace=context.keep_trace)
        return result

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
        self._apply_recovery_action(surface, matched.recovery, resolved_inputs, context)
        recoveries_applied.append(RecoveryApplied(code=matched.code, step_id=step.id, attempts=attempts))

        if matched.then == "escalate":
            return ("escalate", "ESCALATE_DIRECTIVE")
        if matched.then == "restart_from":
            assert matched.restart_step_id is not None
            return ("restart_from", matched.restart_step_id)
        if matched.then == "retry_step":
            return ("retry_step",)
        return ("continue",)

    def _gated_act(self, surface: Surface, action: Action, context: ReplayContext):
        """The one choke point (CLAUDE.md invariant 3): every action from
        the replay path -- a step's own action, and every action a recovery
        takes on its behalf, including re_authenticate's sub-actions --
        passes through here, never surface.act() directly. Returns None on
        denial rather than raising, since AllowAllGate never denies and a
        richer refusal path is step 6's job.
        """
        decision = self._policy_gate.check(action, context)
        if not decision.allowed:
            return None
        return surface.act(action)

    def _apply_recovery_action(
        self,
        surface: Surface,
        recovery_action: RecoveryAction,
        resolved_inputs: dict[str, Any],
        context: ReplayContext,
    ) -> None:
        if recovery_action.action == "re_authenticate":
            self._reauthenticate(surface, context)
            return
        value = substitute(recovery_action.value, resolved_inputs) if recovery_action.value else recovery_action.value
        self._gated_act(
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
            try:
                outputs_extracted[output.name] = apply_transform(output.source.transform, raw)
            except TransformError:
                continue
            if output.sensitive:
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
    ) -> ReplayResult:
        # raise_intervention() already closed the surface if the session
        # wasn't held -- nothing left to do here but build the result.
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
            duration_ms=self._elapsed_ms(start),
            steps_completed=completed,
        )
        evidence.write_result(result, sensitive_output_names)
        return result
