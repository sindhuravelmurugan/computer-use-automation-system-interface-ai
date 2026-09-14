from __future__ import annotations

import json

from tests.agent.conftest import FakeSurface, ScriptedLLMClient
from tests.policy.conftest import make_config

from src.agent.loop import DiscoveryAgent
from src.agent.trace import DISCOVERY_FILE, TRACE_FILE, TraceRecorder
from src.agent.types import AgentAction
from src.policy.config import AllowlistConfig
from src.policy.gate import ConfiguredPolicyGate
from src.policy.redaction import Redactor
from src.replay.evidence import EvidenceWriter

FAKE_APP_CONFIG = make_config(
    allowlist=AllowlistConfig(
        domains=("fake",),
        routes=("/start", "/start/result"),
        action_kinds=("navigate", "click", "type", "select", "read", "wait_for", "assert"),
    )
)


def _run(tmp_path, actions):
    surface = FakeSurface()
    llm = ScriptedLLMClient(actions)
    redactor = Redactor(FAKE_APP_CONFIG.redaction)
    evidence = EvidenceWriter("test-run", base_dir=tmp_path, redactor=redactor)
    trace = TraceRecorder(evidence)
    agent = DiscoveryAgent(
        surface, llm, policy_gate=ConfiguredPolicyGate(FAKE_APP_CONFIG), evidence_run_id="test-run", trace=trace,
    )
    result = agent.run("test goal", "http://fake/start", max_steps=5)
    trace_text = (tmp_path / "test-run" / TRACE_FILE).read_text()
    discovery_text = (tmp_path / "test-run" / DISCOVERY_FILE).read_text()
    return result, trace_text, discovery_text


def test_sensitive_typed_value_never_appears_in_trace_even_in_the_declaring_event(tmp_path):
    result, trace_text, _ = _run(
        tmp_path,
        [
            AgentAction(kind="type", ref="n1", value="SECRET123", sensitive=True),
            AgentAction(kind="done", summary="done"),
        ],
    )
    assert result.stop_reason == "SUCCESS"
    assert "SECRET123" not in trace_text
    assert "REDACTED" in trace_text


def test_non_sensitive_value_is_not_redacted(tmp_path):
    result, trace_text, _ = _run(
        tmp_path,
        [
            AgentAction(kind="type", ref="n1", value="plain-value", sensitive=False),
            AgentAction(kind="done", summary="done"),
        ],
    )
    assert result.stop_reason == "SUCCESS"
    assert "plain-value" in trace_text


def test_discovery_log_never_carries_raw_values_regardless_of_sensitivity(tmp_path):
    _, _, discovery_text = _run(
        tmp_path,
        [
            AgentAction(kind="type", ref="n1", value="SECRET123", sensitive=True),
            AgentAction(kind="done", summary="done"),
        ],
    )
    assert "SECRET123" not in discovery_text


def test_read_target_value_never_leaks_in_the_observation_that_precedes_reading_it(tmp_path):
    """The harder case: a read target's value is already on the page
    *before* the model ever declares it sensitive -- the model's decision
    is what carries the sensitivity flag, but the observation showing that
    value exists a step earlier than the decision. If sensitivity isn't
    known before that observation is logged, the value leaks regardless
    of what happens afterward.
    """
    result, trace_text, _ = _run(
        tmp_path,
        [
            AgentAction(kind="type", ref="n1", value="go"),
            AgentAction(kind="read", ref="n2", output="balance", type="money", sensitive=True),
            AgentAction(kind="done", summary="done"),
        ],
    )
    assert result.stop_reason == "SUCCESS"
    assert "9,999.99" not in trace_text
    assert "REDACTED" in trace_text


def test_subsequent_observation_scrubs_a_value_declared_sensitive_earlier(tmp_path):
    """The typed value keeps reappearing in the field's own node.value on
    every later observation -- it must stay redacted there too, not just
    in the one decision event that declared it."""
    result, trace_text, _ = _run(
        tmp_path,
        [
            AgentAction(kind="type", ref="n1", value="SECRET123", sensitive=True),
            AgentAction(kind="done", summary="done"),
        ],
    )
    events = [json.loads(line) for line in trace_text.splitlines()]
    later_observations = [e for e in events if e.get("event") == "observation" and e.get("step", 0) >= 1]
    assert later_observations, "expected at least one observation after the sensitive type action"
    for event in later_observations:
        assert "SECRET123" not in event["text"]
