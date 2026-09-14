"""The CLI. `discover` is the end-to-end thread docs/discovery-spec.md asks
for: a goal in, an LLM-driven run, a saved draft capability artifact out.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import typer
from dotenv import load_dotenv

from src.agent.factory import build_llm_client
from src.agent.loop import DEFAULT_MAX_STEPS, DEFAULT_TIMEOUT_S, DiscoveryAgent
from src.agent.trace import TraceRecorder
from src.compiler.compiler import compile_trace
from src.compiler.probe import generate_invalid_probe_value, run_probe
from src.escalation.operator_app import DEFAULT_PORT as DEFAULT_OPERATOR_PORT
from src.escalation.operator_app import create_app as create_operator_app
from src.policy import ConfiguredPolicyGate, PolicyConfig, Redactor
from src.policy.config import DEFAULT_CONFIG_PATH
from src.replay.context import ReplayContext
from src.replay.detectors import describe_detector
from src.replay.engine import ReplayEngine
from src.replay.evidence import EvidenceWriter
from src.schema.artifact import CapabilityArtifact
from src.surface.web import WebSurface

DEFAULT_ARTIFACTS_DIR = "artifacts"

load_dotenv()

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _main() -> None:
    """Discover a capability by driving the live app with an LLM, replay it
    deterministically with none, or review what's been saved."""


@app.command()
def discover(
    goal: str = typer.Option(..., "--goal", help="What the discovery run should accomplish."),
    target: str = typer.Option(..., "--target", help="Entry point URL to start the run from."),
    max_steps: int = typer.Option(DEFAULT_MAX_STEPS, "--max-steps", help="Hard cap on steps."),
    timeout: float = typer.Option(DEFAULT_TIMEOUT_S, "--timeout", help="Wall-clock seconds before TIMEOUT."),
    provider: str = typer.Option(None, "--provider", help="gemini|anthropic; defaults to LLM_PROVIDER."),
    policy_config: str = typer.Option(str(DEFAULT_CONFIG_PATH), "--policy-config", help="Path to policy.json."),
    keep_trace: bool = typer.Option(False, "--keep-trace", help="Keep the Playwright trace.zip."),
    headless: bool = typer.Option(True, "--headless/--headed"),
    probe: bool = typer.Option(True, "--probe/--no-probe", help="Run the probe pass on success."),
    run_id: str = typer.Option(None, "--run-id", help="Defaults to a generated id."),
) -> None:
    run_id = run_id or f"discover-{int(time.time())}"

    config = PolicyConfig.load(policy_config)
    llm = build_llm_client(provider)
    evidence = EvidenceWriter(run_id, redactor=Redactor(config.redaction))
    trace = TraceRecorder(evidence)
    surface = WebSurface(headless=headless)
    gate = ConfiguredPolicyGate(config)

    agent = DiscoveryAgent(surface, llm, policy_gate=gate, evidence_run_id=run_id, trace=trace)
    result = agent.run(goal, target, max_steps=max_steps, timeout_s=timeout, keep_trace=keep_trace)

    typer.echo(f"run_id={run_id} stop_reason={result.stop_reason}")

    if result.stop_reason != "SUCCESS":
        if result.intervention_path:
            typer.echo(f"intervention written to {result.intervention_path}")
        raise typer.Exit(code=1)

    model_name = getattr(llm, "_model", provider or "unknown")
    draft = compile_trace(result, model_name=model_name)

    if probe and draft.artifact.inputs:
        probed_name = draft.artifact.inputs[0].name
        original_value = draft.param_values.get(probed_name, "")
        invalid_value = generate_invalid_probe_value(original_value)
        probe_surface = WebSurface(headless=headless)
        probe_result = run_probe(
            probe_surface,
            draft.artifact,
            param_values=draft.param_values,
            invalid_value=invalid_value,
            evidence=evidence,
        )
        typer.echo(f"probe: {probe_result.reason}")
        if probe_result.outcome is not None:
            draft.artifact.outcomes.append(probe_result.outcome)

    artifact_payload = json.loads(draft.artifact.model_dump_json())
    artifact_path = evidence.write_json("artifact.draft.json", artifact_payload)
    typer.echo(f"draft artifact written to {artifact_path}")

    if draft.review_notes:
        typer.echo("review notes:")
        for note in draft.review_notes:
            typer.echo(f"  - {note}")


def _iter_artifacts(artifacts_dir: str) -> list[CapabilityArtifact]:
    """Top-level *.json only -- artifacts/overrides/ holds tenant patches,
    not standalone capabilities, and a directory listing shouldn't try to
    parse those as one.
    """
    artifacts = []
    for path in sorted(Path(artifacts_dir).glob("*.json")):
        artifacts.append(CapabilityArtifact.model_validate(json.loads(path.read_text(encoding="utf-8"))))
    return artifacts


def _load_artifact_by_id(capability_id: str, artifacts_dir: str) -> CapabilityArtifact | None:
    for artifact in _iter_artifacts(artifacts_dir):
        if artifact.capability.id == capability_id:
            return artifact
    return None


def _parse_inputs(pairs: list[str]) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise typer.BadParameter(f"--input must be key=value, got {pair!r}")
        key, _, value = pair.partition("=")
        inputs[key] = value
    return inputs


@app.command()
def replay(
    capability: str = typer.Option(..., "--capability", help="Capability id, e.g. member.lookup_savings_balance."),
    input_: list[str] = typer.Option([], "--input", help="key=value, repeatable (e.g. --input member_id=10001)."),
    tenant: str = typer.Option(None, "--tenant", help="Tenant id -- looks up artifacts/overrides/<capability>.<tenant>.json."),
    artifacts_dir: str = typer.Option(DEFAULT_ARTIFACTS_DIR, "--artifacts-dir"),
    policy_config: str = typer.Option(str(DEFAULT_CONFIG_PATH), "--policy-config", help="Path to policy.json."),
    attended: bool = typer.Option(False, "--attended/--unattended", help="Unattended: risky steps deny; escalations close the session."),
    allow_risky: bool = typer.Option(False, "--allow-risky", help="Opt in to risky steps without operator approval."),
    keep_trace: bool = typer.Option(False, "--keep-trace", help="Keep the Playwright trace.zip."),
    headless: bool = typer.Option(True, "--headless/--headed"),
    run_id: str = typer.Option(None, "--run-id", help="Defaults to a generated id."),
) -> None:
    """Deterministic replay of a saved capability artifact -- no LLM in this
    path (CLAUDE.md invariant 1). ``status`` is one of success,
    business_outcome, or failure; only failure exits non-zero.
    """
    run_id = run_id or f"replay-{int(time.time())}"

    artifact = _load_artifact_by_id(capability, artifacts_dir)
    if artifact is None:
        typer.echo(f"no artifact found for capability {capability!r} under {artifacts_dir}/", err=True)
        raise typer.Exit(code=1)

    inputs = _parse_inputs(input_)
    config = PolicyConfig.load(policy_config)
    gate = ConfiguredPolicyGate(config)
    engine = ReplayEngine(lambda: WebSurface(headless=headless), policy_gate=gate, redaction_config=config.redaction)
    context = ReplayContext(run_id=run_id, tenant_id=tenant, attended=attended, allow_risky=allow_risky, keep_trace=keep_trace)

    result = engine.run(artifact, inputs, context)

    typer.echo(f"run_id={run_id} status={result.status}")
    if result.status == "success":
        typer.echo(f"outputs: {json.dumps(result.outputs, default=str, indent=2)}")
    elif result.status == "business_outcome":
        typer.echo(f"outcome: {result.outcome.code}")
        typer.echo(f"returns: {json.dumps(result.outcome.returns, indent=2)}")
    else:
        typer.echo(f"error: {result.error.code}")
        typer.echo(f"  step_id:  {result.error.step_id}")
        typer.echo(f"  expected: {result.error.expected}")
        typer.echo(f"  observed: {result.error.observed}")
        if result.error.evidence:
            typer.echo(f"  evidence: {result.error.evidence}")

    typer.echo(f"evidence: evidence/{run_id}/")

    if result.status == "failure":
        raise typer.Exit(code=1)


@app.command()
def capabilities(
    artifacts_dir: str = typer.Option(DEFAULT_ARTIFACTS_DIR, "--artifacts-dir"),
) -> None:
    """List saved capability artifacts with their typed inputs/outputs --
    the minimal agent-facing catalog (docs/deliverables-spec.md §2).
    """
    artifacts = _iter_artifacts(artifacts_dir)
    if not artifacts:
        typer.echo(f"no artifacts found under {artifacts_dir}/")
        raise typer.Exit(code=1)

    for artifact in artifacts:
        cap = artifact.capability
        typer.echo(f"{cap.id}  v{cap.version}  [{cap.approval_state}]")
        typer.echo(f"  {cap.description}")
        inputs = ", ".join(f"{p.name}:{p.type}{'?' if not p.required else ''}" for p in artifact.inputs) or "(none)"
        outputs = ", ".join(f"{p.name}:{p.type}" for p in artifact.outputs) or "(none)"
        typer.echo(f"  inputs:  {inputs}")
        typer.echo(f"  outputs: {outputs}")
        typer.echo("")


@app.command()
def show(
    capability_id: str = typer.Argument(..., help="Capability id, e.g. member.lookup_savings_balance."),
    artifacts_dir: str = typer.Option(DEFAULT_ARTIFACTS_DIR, "--artifacts-dir"),
) -> None:
    """Pretty-print one artifact for human review -- the schema's
    "reviewable by a human" requirement, without hand-editing JSON.
    """
    artifact = _load_artifact_by_id(capability_id, artifacts_dir)
    if artifact is None:
        typer.echo(f"no artifact found for capability {capability_id!r} under {artifacts_dir}/", err=True)
        raise typer.Exit(code=1)

    cap = artifact.capability
    typer.echo(f"{cap.id}  v{cap.version}  [{cap.approval_state}]")
    typer.echo(f"{cap.name}")
    typer.echo(f"{cap.description}")
    typer.echo("")
    typer.echo(f"surface:  {artifact.surface.type} @ {artifact.surface.entry_point}")
    typer.echo(
        f"recorded: {artifact.provenance.recorded_at} by {artifact.provenance.recorded_by} "
        f"(model={artifact.provenance.model}, against={artifact.provenance.recorded_against})"
    )
    typer.echo("")

    typer.echo("inputs:")
    for p in artifact.inputs:
        flags = []
        if p.required:
            flags.append("required")
        if p.sensitive:
            flags.append("sensitive")
        typer.echo(f"  {p.name}: {p.type} [{', '.join(flags) or 'optional'}] -- {p.description}")

    typer.echo("outputs:")
    for o in artifact.outputs:
        flags = []
        if o.required:
            flags.append("required")
        if o.sensitive:
            flags.append("sensitive")
        typer.echo(f"  {o.name}: {o.type} [{', '.join(flags) or 'optional'}] -- {o.description}")

    typer.echo("")
    typer.echo(f"steps ({len(artifact.steps)}):")
    for step in artifact.steps:
        target = f" -> {step.target.description}" if step.target else ""
        checkpoint = f"  [checkpoint: {describe_detector(step.checkpoint)}]" if step.checkpoint else ""
        typer.echo(f"  {step.id}: {step.action}{target}  (risk={step.risk}){checkpoint}")

    if artifact.outcomes:
        typer.echo("")
        typer.echo("business outcomes:")
        for outcome in artifact.outcomes:
            typer.echo(f"  {outcome.code}: {describe_detector(outcome.detect)} -> returns {outcome.returns}")

    if artifact.recoveries:
        typer.echo("")
        typer.echo("recoveries:")
        for recovery in artifact.recoveries:
            typer.echo(
                f"  {recovery.code}: {describe_detector(recovery.detect)} "
                f"-> {recovery.recovery.action}, then={recovery.then} (max_attempts={recovery.max_attempts})"
            )

    typer.echo("")
    typer.echo(f"success: {describe_detector(artifact.success.checkpoint)}")
    typer.echo(f"  require_all_outputs: {artifact.success.require_all_outputs}")


@app.command()
def operator(
    evidence_dir: str = typer.Option("evidence", "--evidence-dir", help="Where control.json/human_actions.jsonl live."),
    port: int = typer.Option(DEFAULT_OPERATOR_PORT, "--port", help="Port for the operator Flask app."),
) -> None:
    """The mock operator surface (docs/escalation-spec.md §7): list open
    intervention requests, claim one, and release it back when done. A
    separate process/port from both the target app and this CLI's own
    discovery runs -- it only reads and writes evidence/{run_id}/control.json.
    """
    create_operator_app(evidence_dir).run(port=port, debug=False)


if __name__ == "__main__":
    app()
