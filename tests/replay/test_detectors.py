from __future__ import annotations

from src.replay.detectors import describe_detector, evaluate_detector, in_scope
from src.schema.common import (
    A11yStrategy,
    AllOfDetector,
    AnyOfDetector,
    ElementAbsentDetector,
    ElementPresentDetector,
    Target,
    TextMatchesDetector,
    UrlMatchesDetector,
    ValueEqualsDetector,
)


def target(name: str, role: str = "text") -> Target:
    return Target(description=f"{role} {name}", strategies=[A11yStrategy(role=role, name=name)])


def test_in_scope_any_matches_every_step():
    assert in_scope(["any"], "step_001") is True
    assert in_scope(["any"], "step_999") is True


def test_in_scope_explicit_list():
    assert in_scope(["step_003"], "step_003") is True
    assert in_scope(["step_003"], "step_004") is False


def test_element_present_true_when_resolved(make_node, make_observation):
    obs = make_observation(nodes=[make_node(role="dialog", name="Disclosure")])
    detector = ElementPresentDetector(target=target("Disclosure", "dialog"))
    assert evaluate_detector(detector, obs) is True


def test_element_present_false_when_not_found(make_observation):
    obs = make_observation(nodes=[])
    detector = ElementPresentDetector(target=target("Disclosure", "dialog"))
    assert evaluate_detector(detector, obs) is False


def test_element_present_false_when_ambiguous(make_node, make_observation):
    obs = make_observation(nodes=[make_node(ref="n1", role="text", name="dup"), make_node(ref="n2", role="text", name="dup")])
    detector = ElementPresentDetector(target=target("dup"))
    assert evaluate_detector(detector, obs) is False


def test_element_absent_true_when_not_found(make_observation):
    obs = make_observation(nodes=[])
    detector = ElementAbsentDetector(target=target("Disclosure", "dialog"))
    assert evaluate_detector(detector, obs) is True


def test_element_absent_false_when_ambiguous_not_treated_as_absence(make_node, make_observation):
    """Ambiguous is not proof of absence -- ElementAbsent should only be
    true on a clean not_found, never on an uncertain match."""
    obs = make_observation(nodes=[make_node(ref="n1", role="text", name="dup"), make_node(ref="n2", role="text", name="dup")])
    detector = ElementAbsentDetector(target=target("dup"))
    assert evaluate_detector(detector, obs) is False


def test_text_matches_searches_all_node_names(make_node, make_observation):
    obs = make_observation(nodes=[make_node(role="alert", name="No member found for that ID.")])
    detector = TextMatchesDetector(pattern="No member found")
    assert evaluate_detector(detector, obs) is True


def test_text_matches_false_when_absent(make_node, make_observation):
    obs = make_observation(nodes=[make_node(role="text", name="Account summary")])
    detector = TextMatchesDetector(pattern="No member found")
    assert evaluate_detector(detector, obs) is False


def test_value_equals_compares_node_value_first(make_node, make_observation):
    obs = make_observation(nodes=[make_node(role="textbox", name="Member ID", value="10001")])
    detector = ValueEqualsDetector(target=target("Member ID", "textbox"), expected="10001")
    assert evaluate_detector(detector, obs) is True


def test_value_equals_falls_back_to_name_when_value_is_none(make_node, make_observation):
    obs = make_observation(nodes=[make_node(role="text", name="4,832.10", value=None)])
    detector = ValueEqualsDetector(target=target("4,832.10"), expected="4,832.10")
    assert evaluate_detector(detector, obs) is True


def test_value_equals_false_when_target_not_found(make_observation):
    obs = make_observation(nodes=[])
    detector = ValueEqualsDetector(target=target("Member ID", "textbox"), expected="10001")
    assert evaluate_detector(detector, obs) is False


def test_url_matches(make_observation):
    obs = make_observation(url="http://127.0.0.1:5001/login?expired=1")
    assert evaluate_detector(UrlMatchesDetector(pattern=".*/login.*"), obs) is True
    assert evaluate_detector(UrlMatchesDetector(pattern=".*/logout.*"), obs) is False


def test_any_of_true_if_one_matches(make_node, make_observation):
    obs = make_observation(nodes=[make_node(role="alert", name="No member found")])
    detector = AnyOfDetector(
        any_of=[
            TextMatchesDetector(pattern="never appears"),
            TextMatchesDetector(pattern="No member found"),
        ]
    )
    assert evaluate_detector(detector, obs) is True


def test_all_of_requires_every_sub_detector(make_node, make_observation):
    obs = make_observation(nodes=[make_node(role="heading", name="Account summary")])
    passing = AllOfDetector(all_of=[TextMatchesDetector(pattern="Account summary")])
    assert evaluate_detector(passing, obs) is True

    failing = AllOfDetector(
        all_of=[TextMatchesDetector(pattern="Account summary"), TextMatchesDetector(pattern="nope")]
    )
    assert evaluate_detector(failing, obs) is False


def test_describe_detector_is_human_readable():
    text = describe_detector(ElementPresentDetector(target=target("Search", "button")))
    assert "button Search" in text
