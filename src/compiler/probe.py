"""The probe pass (docs/discovery-spec.md §7). Discovery only ever walks
the happy path -- it never sees "no member found," so it cannot discover
the error taxonomy on its own. This re-executes the compiled steps once
with a single declared parameter set to a well-formed but nonexistent
value, watches where the flow diverges from what the checkpoints expect,
and captures the distinguishing node at that point (typically an alert
region) as a candidate outcome.

Bounds, enforced here rather than left to the caller:
- one probe run, not a search
- refuses outright if the artifact contains any `risky` step
- only ever varies a declared *input* -- never injects a failure
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.replay.detectors import evaluate_detector
from src.replay.evidence import EvidenceWriter
from src.replay.login import DEFAULT_CREDENTIALS, perform_login
from src.replay.templating import substitute, substitute_detector
from src.schema.artifact import CapabilityArtifact
from src.schema.common import TextMatchesDetector
from src.schema.outcomes import Outcome
from src.surface.protocol import Surface
from src.surface.types import Action, Observation, UINode, WaitSpec

PROBE_LOG_FILE = "probe.jsonl"
_ALERT_ROLES = ("alert", "alertdialog", "status")

_STOPWORDS = frozenset({"a", "an", "the", "for", "to", "of", "is", "are", "that", "this", "your", "you", "in", "on", "at", "was"})
_NO_X_FOUND_RE = re.compile(r"\bno\s+(\w+)\s+found\b", re.IGNORECASE)


@dataclass
class ProbeOutcome:
    outcome: Outcome | None
    reason: str  # why an outcome was or wasn't produced, for the log/reviewer


def generate_invalid_probe_value(original: str) -> str:
    """A well-formed value that violates nothing but the data (docs/
    discovery-spec.md §7): same shape as what was actually typed, but a
    value the seed data won't contain. All-digit IDs (the common case for
    this kind of lookup) become a same-length run of 9s, which sorts well
    outside small sequential seed ranges; anything else gets a suffix that
    keeps the original recognizable while being new.
    """
    if original.isdigit():
        return "9" * len(original)
    return f"{original}_probe_nonexistent"


def run_probe(
    surface: Surface,
    artifact: CapabilityArtifact,
    *,
    param_values: dict[str, str],
    invalid_value: str,
    evidence: EvidenceWriter,
    credentials: tuple[str, str] = DEFAULT_CREDENTIALS,
) -> ProbeOutcome:
    if any(step.risk == "risky" for step in artifact.steps):
        return ProbeOutcome(None, "refused: artifact contains a risky step")
    if not artifact.inputs:
        return ProbeOutcome(None, "refused: no declared input to probe")

    probed_param = artifact.inputs[0].name
    run_inputs = dict(param_values)
    run_inputs[probed_param] = invalid_value

    evidence.log({"event": "probe_start", "probed_param": probed_param, "invalid_value": invalid_value}, file=PROBE_LOG_FILE)

    probe_run_id = f"{evidence.run_id}-probe"
    surface.open(artifact.surface.entry_point, probe_run_id)

    # A fresh browser context has no session -- same bootstrap every other
    # entry point into this app needs (see src.replay.login).
    current_url = surface.observe().page.url
    if "/login" in current_url:
        perform_login(surface.act, current_url, credentials)
        surface.act(Action(kind="navigate", url=artifact.surface.entry_point, wait=WaitSpec()))
        evidence.log({"event": "bootstrap_login"}, file=PROBE_LOG_FILE)

    divergence_step_id: str | None = None
    divergence_observation: Observation | None = None

    try:
        for step in artifact.steps:
            if step.target is not None:
                resolution = surface.resolve(step.target)
                evidence.log({"event": "resolve", "step_id": step.id, "status": resolution.status}, file=PROBE_LOG_FILE)
                if resolution.status != "resolved":
                    divergence_step_id = step.id
                    divergence_observation = surface.observe()
                    break

            value = substitute(step.value, run_inputs) if step.value else step.value
            action = Action(
                kind=step.action,
                target=step.target,
                value=value,
                url=value if step.action == "navigate" else None,
                wait=WaitSpec(),
            )
            result = surface.act(action)
            evidence.log({"event": "action", "step_id": step.id, "kind": step.action, "ok": result.ok}, file=PROBE_LOG_FILE)
            observation = result.observation_after

            if step.checkpoint is not None:
                checkpoint = substitute_detector(step.checkpoint, run_inputs)
                passed = evaluate_detector(checkpoint, observation)
                evidence.log({"event": "checkpoint", "step_id": step.id, "result": "pass" if passed else "fail"}, file=PROBE_LOG_FILE)
                if not passed:
                    divergence_step_id = step.id
                    divergence_observation = observation
                    break
    finally:
        surface.close(keep_trace=False)

    if divergence_step_id is None or divergence_observation is None:
        evidence.log({"event": "probe_no_divergence"}, file=PROBE_LOG_FILE)
        return ProbeOutcome(None, "no divergence: the invalid value did not change the flow's outcome")

    alert_node = _find_alert_node(divergence_observation)
    if alert_node is None:
        evidence.log({"event": "probe_no_distinguishing_node", "step_id": divergence_step_id}, file=PROBE_LOG_FILE)
        return ProbeOutcome(None, f"diverged at {divergence_step_id} but found no alert-region node to ground a detector in")

    pattern = _extract_pattern(alert_node.name)
    outcome = Outcome(
        code=_derive_outcome_code(alert_node.name),
        kind="business",
        description=f"Derived by the probe pass from the divergence at {divergence_step_id}: {alert_node.name!r}",
        detect=TextMatchesDetector(pattern=re.escape(pattern)),
        check_after=[divergence_step_id],
        returns={"found": False},
        terminal=True,
        origin="probed",
    )
    evidence.log({"event": "probe_outcome", "code": outcome.code, "step_id": divergence_step_id}, file=PROBE_LOG_FILE)
    return ProbeOutcome(outcome, f"probed successfully at {divergence_step_id}")


def _find_alert_node(observation: Observation) -> UINode | None:
    return next((n for n in observation.nodes if n.role in _ALERT_ROLES), None)


def _extract_pattern(text: str) -> str:
    return text.split(".")[0].split(",")[0].strip()


def _derive_outcome_code(text: str) -> str:
    match = _NO_X_FOUND_RE.search(text)
    if match:
        return f"{match.group(1).upper()}_NOT_FOUND"
    words = re.findall(r"[a-zA-Z]+", text.lower())
    significant = [w for w in words if w not in _STOPWORDS][:4] or ["outcome"]
    return "_".join(significant).upper()
