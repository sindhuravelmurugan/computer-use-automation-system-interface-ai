from __future__ import annotations

from src.agent.types import AgentAction
from src.compiler.checkpoints import infer_checkpoint
from src.schema.common import ElementPresentDetector, UrlMatchesDetector, ValueEqualsDetector


def test_navigate_infers_url_matches(make_trace_step):
    step = make_trace_step(
        0, AgentAction(kind="navigate", url="http://x/members/search"),
        after_kwargs={"url": "http://127.0.0.1:5001/members/search"},
    )
    detector, note = infer_checkpoint(step)
    assert isinstance(detector, UrlMatchesDetector)
    assert "/members/search" in detector.pattern
    assert note is not None


def test_type_infers_value_equals(make_trace_step, make_node):
    node = make_node(role="textbox", name="Member ID", dom_hint="#txt")
    step = make_trace_step(0, AgentAction(kind="type", ref="n1", value="10001"), node=node)
    detector, note = infer_checkpoint(step)
    assert isinstance(detector, ValueEqualsDetector)
    assert detector.expected == "10001"


def test_type_with_declared_parameter_infers_templated_expected(make_trace_step, make_node):
    """Regression: the checkpoint must use the same {{param}} placeholder
    the compiled step's own `value` field uses, not the literal value
    observed during this one discovery run -- otherwise the checkpoint
    fails on every input except the one that happened to be typed here."""
    node = make_node(role="textbox", name="Member ID", dom_hint="#txt")
    step = make_trace_step(
        0, AgentAction(kind="type", ref="n1", value="10001", parameter="member_id"), node=node
    )
    detector, note = infer_checkpoint(step)
    assert isinstance(detector, ValueEqualsDetector)
    assert detector.expected == "{{member_id}}"


def test_click_causing_heading_change_infers_element_present(make_trace_step):
    step = make_trace_step(
        0, AgentAction(kind="click", ref="n1"),
        before_kwargs={"heading": None},
        after_kwargs={"heading": "Account summary"},
    )
    detector, note = infer_checkpoint(step)
    assert isinstance(detector, ElementPresentDetector)
    assert detector.target.strategies[0].name == "Account summary"


def test_click_revealing_content_infers_element_present_on_new_node(make_trace_step, make_node):
    existing = make_node(ref="n1", role="text", name="existing")
    new_dialog = make_node(ref="n2", role="dialog", name="Disclosure")
    step = make_trace_step(
        0, AgentAction(kind="click", ref="n3"),
        before_kwargs={"nodes": [existing]},
        after_kwargs={"nodes": [existing, new_dialog]},
    )
    detector, note = infer_checkpoint(step)
    assert isinstance(detector, ElementPresentDetector)
    assert detector.target.strategies[0].name == "Disclosure"


def test_click_with_no_change_infers_nothing(make_trace_step, make_node):
    existing = make_node(ref="n1", role="text", name="existing")
    step = make_trace_step(
        0, AgentAction(kind="click", ref="n1"),
        before_kwargs={"nodes": [existing]},
        after_kwargs={"nodes": [existing]},
    )
    detector, note = infer_checkpoint(step)
    assert detector is None
    assert note is None


def test_read_infers_nothing(make_trace_step, make_node):
    node = make_node(role="text", name="4,832.10")
    step = make_trace_step(0, AgentAction(kind="read", ref="n1", output="balance", type="money"), node=node)
    detector, note = infer_checkpoint(step)
    assert detector is None
