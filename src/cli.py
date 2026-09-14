"""The CLI. `discover` is the end-to-end thread docs/discovery-spec.md asks
for: a goal in, an LLM-driven run, a saved draft capability artifact out.
"""

from __future__ import annotations

import json
import time

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
from src.replay.evidence import EvidenceWriter
from src.surface.web import WebSurface

load_dotenv()

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _main() -> None:
    """Discovery agent and compiler CLI (docs/discovery-spec.md)."""


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
