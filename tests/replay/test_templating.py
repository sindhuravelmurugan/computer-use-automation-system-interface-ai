from __future__ import annotations

import pytest

from src.replay.templating import substitute, substitute_detector
from src.schema.common import A11yStrategy, AnyOfDetector, Target, TextMatchesDetector, ValueEqualsDetector


def target(name: str = "Member ID") -> Target:
    return Target(description="x", strategies=[A11yStrategy(role="textbox", name=name)])


def test_substitute_replaces_known_placeholder():
    assert substitute("{{member_id}}", {"member_id": "10001"}) == "10001"


def test_substitute_handles_multiple_placeholders():
    assert substitute("{{a}}-{{b}}", {"a": "1", "b": "2"}) == "1-2"


def test_substitute_leaves_plain_text_untouched():
    assert substitute("Search", {"member_id": "10001"}) == "Search"


def test_substitute_raises_on_undeclared_input():
    with pytest.raises(KeyError):
        substitute("{{unknown}}", {"member_id": "10001"})


def test_substitute_detector_value_equals():
    detector = ValueEqualsDetector(target=target(), expected="{{member_id}}")
    result = substitute_detector(detector, {"member_id": "10001"})
    assert isinstance(result, ValueEqualsDetector)
    assert result.expected == "10001"
    # original untouched
    assert detector.expected == "{{member_id}}"


def test_substitute_detector_text_matches():
    detector = TextMatchesDetector(pattern="Member {{member_id}}")
    result = substitute_detector(detector, {"member_id": "10001"})
    assert result.pattern == "Member 10001"


def test_substitute_detector_recurses_into_any_of():
    detector = AnyOfDetector(
        any_of=[
            ValueEqualsDetector(target=target(), expected="{{member_id}}"),
            TextMatchesDetector(pattern="static"),
        ]
    )
    result = substitute_detector(detector, {"member_id": "10001"})
    assert result.any_of[0].expected == "10001"
    assert result.any_of[1].pattern == "static"
