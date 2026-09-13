from __future__ import annotations

from tests.agent.conftest import FakeSurface, ScriptedLLMClient

from src.agent.loop import DiscoveryAgent
from src.agent.trace import TraceRecorder
from src.agent.types import AgentAction
from src.replay.evidence import EvidenceWriter
from src.replay.policy import AllowAllGate, AllowlistPolicyGate


def _agent(tmp_path, actions, *, run_id="test-run", allowlist=None, policy_gate=None):
    surface = FakeSurface()
    llm = ScriptedLLMClient(actions)
    evidence = EvidenceWriter(run_id, base_dir=tmp_path)
    trace = TraceRecorder(evidence)
    agent = DiscoveryAgent(
        surface, llm,
        policy_gate=policy_gate or AllowAllGate(),
        evidence_run_id=run_id, trace=trace,
        entry_allowlist=allowlist or ["http://fake"],
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
    agent, surface = _agent(tmp_path, [AgentAction(kind="done", summary="n/a")], allowlist=["http://allowed-only"])
    result = agent.run("goal", "http://fake/start", max_steps=5)
    assert result.stop_reason == "POLICY"
    assert surface.opened_entry_point is None


def test_disallowed_mid_run_navigate_is_denied_by_allowlist_gate(tmp_path):
    gate = AllowlistPolicyGate(["http://fake"])
    actions = [
        AgentAction(kind="navigate", url="https://evil.example.com/page"),
        AgentAction(kind="navigate", url="https://evil.example.com/page"),
    ]
    agent, _ = _agent(tmp_path, actions, allowlist=["http://fake"], policy_gate=gate)
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
