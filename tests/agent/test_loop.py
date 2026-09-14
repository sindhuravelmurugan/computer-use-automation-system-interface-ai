from __future__ import annotations

from tests.agent.conftest import FakeSurface, ScriptedLLMClient
from tests.policy.conftest import make_config

from src.agent.loop import DiscoveryAgent
from src.agent.trace import TraceRecorder
from src.agent.types import AgentAction
from src.policy.config import AllowlistConfig
from src.policy.gate import AllowAllGate, ConfiguredPolicyGate
from src.replay.evidence import EvidenceWriter

# The fake surface's whole "app" lives at this one domain/route pair --
# a policy config just permissive enough for it, distinct from the real
# app's config so tests here don't depend on config/policy.json's contents.
FAKE_APP_CONFIG = make_config(
    allowlist=AllowlistConfig(
        domains=("fake",),
        routes=("/start", "/start/result"),
        action_kinds=("navigate", "click", "type", "select", "read", "wait_for", "assert"),
    )
)


def _agent(tmp_path, actions, *, run_id="test-run", policy_gate=None):
    surface = FakeSurface()
    llm = ScriptedLLMClient(actions)
    evidence = EvidenceWriter(run_id, base_dir=tmp_path)
    trace = TraceRecorder(evidence)
    agent = DiscoveryAgent(
        surface, llm,
        policy_gate=policy_gate or ConfiguredPolicyGate(FAKE_APP_CONFIG),
        evidence_run_id=run_id, trace=trace,
    )
    return agent, surface


def test_max_steps_stops_and_never_reports_success(tmp_path):
    # A repeated "type" of different values keeps the model busy without
    # ever calling done, so MAX_STEPS is the only way this ends.
    actions = [AgentAction(kind="type", ref="n1", value=f"v{i}") for i in range(10)]
    agent, _ = _agent(tmp_path, actions)
    result = agent.run("goal", "http://fake/start", max_steps=2)
    assert result.stop_reason == "MAX_STEPS"
    assert result.success is False


def test_invalid_ref_is_rejected_without_acting_and_logged(tmp_path):
    actions = [
        AgentAction(kind="click", ref="does-not-exist"),
        AgentAction(kind="type", ref="n1", value="ok"),
        AgentAction(kind="done", summary="done"),
    ]
    agent, surface = _agent(tmp_path, actions)
    result = agent.run("goal", "http://fake/start", max_steps=5)
    assert result.stop_reason == "SUCCESS"
    # the invalid-ref action must not have reached the surface as a type/click
    assert surface._typed_value == "ok"

    trace_text = (tmp_path / "test-run" / "trace.jsonl").read_text()
    assert '"event": "invalid_ref"' in trace_text
    assert '"ref": "does-not-exist"' in trace_text


def test_disallowed_entry_point_is_denied_before_surface_opens(tmp_path):
    # The default test policy config only allows 127.0.0.1:5001/5002 --
    # "fake" isn't in it, so the entry point itself is denied.
    restrictive_gate = ConfiguredPolicyGate(make_config())
    agent, surface = _agent(tmp_path, [AgentAction(kind="done", summary="n/a")], policy_gate=restrictive_gate)
    result = agent.run("goal", "http://fake/start", max_steps=5)
    assert result.stop_reason == "POLICY"
    assert surface.opened_entry_point is None


def test_disallowed_mid_run_navigate_is_denied_by_the_gate(tmp_path):
    actions = [
        AgentAction(kind="navigate", url="https://evil.example.com/page"),
        AgentAction(kind="navigate", url="https://evil.example.com/page"),
    ]
    agent, _ = _agent(tmp_path, actions)
    result = agent.run("goal", "http://fake/start", max_steps=5)
    # two identical denials in a row -> POLICY stop
    assert result.stop_reason == "POLICY"


def test_done_on_the_entry_point_is_rejected_and_run_continues(tmp_path):
    actions = [
        AgentAction(kind="done", summary="premature"),
        AgentAction(kind="type", ref="n1", value="ok"),
        AgentAction(kind="done", summary="real done"),
    ]
    agent, _ = _agent(tmp_path, actions)
    result = agent.run("goal", "http://fake/start", max_steps=5)
    assert result.stop_reason == "SUCCESS"


def test_allow_all_gate_never_denies_the_fake_domain(tmp_path):
    actions = [AgentAction(kind="type", ref="n1", value="go"), AgentAction(kind="done", summary="done")]
    agent, _ = _agent(tmp_path, actions, policy_gate=AllowAllGate())
    result = agent.run("goal", "http://fake/start", max_steps=5)
    assert result.stop_reason == "SUCCESS"
