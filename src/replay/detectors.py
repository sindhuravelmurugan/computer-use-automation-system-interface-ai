"""Detector evaluation: the error taxonomy is data, not code
(docs/replay-spec.md §4, CLAUDE.md invariant 4). The engine matches declared
detectors generically against a *frozen* observation — the one ``act()``
returned for the current step — never by re-querying a live page. That is
what makes this pure and unit-testable without a browser, and what keeps
recoveries/outcomes/checkpoints looking at exactly the state the step
actually produced.

``check_after`` scoping (a step-id list, or "any") also lives here, since
it's part of "does this detector apply right now," not part of "does the
condition hold."
"""

from __future__ import annotations

import re

from src.schema.common import (
    AllOfDetector,
    AnyOfDetector,
    Detector,
    ElementAbsentDetector,
    ElementPresentDetector,
    TextMatchesDetector,
    UrlMatchesDetector,
    ValueEqualsDetector,
)
from src.surface.matching import resolve_bundle
from src.surface.types import Observation


def in_scope(check_after: list[str], step_id: str) -> bool:
    return "any" in check_after or step_id in check_after


def evaluate_detector(detector: Detector, observation: Observation) -> bool:
    if isinstance(detector, ElementPresentDetector):
        return resolve_bundle(detector.target, observation.nodes).status == "resolved"

    if isinstance(detector, ElementAbsentDetector):
        # Ambiguous is not proof of absence -- only a clean not_found is.
        return resolve_bundle(detector.target, observation.nodes).status == "not_found"

    if isinstance(detector, TextMatchesDetector):
        haystack = "\n".join(n.name for n in observation.nodes)
        return re.search(detector.pattern, haystack) is not None

    if isinstance(detector, ValueEqualsDetector):
        res = resolve_bundle(detector.target, observation.nodes)
        if res.status != "resolved":
            return False
        assert res.node is not None
        actual = res.node.value if res.node.value is not None else res.node.name
        return actual == detector.expected

    if isinstance(detector, UrlMatchesDetector):
        return re.search(detector.pattern, observation.page.url) is not None

    if isinstance(detector, AnyOfDetector):
        return any(evaluate_detector(d, observation) for d in detector.any_of)

    if isinstance(detector, AllOfDetector):
        return all(evaluate_detector(d, observation) for d in detector.all_of)

    raise ValueError(f"unknown detector kind: {detector!r}")


def describe_detector(detector: Detector) -> str:
    if isinstance(detector, ElementPresentDetector):
        return f"element present: {detector.target.description}"
    if isinstance(detector, ElementAbsentDetector):
        return f"element absent: {detector.target.description}"
    if isinstance(detector, TextMatchesDetector):
        return f"text matches /{detector.pattern}/"
    if isinstance(detector, ValueEqualsDetector):
        return f"{detector.target.description} == {detector.expected!r}"
    if isinstance(detector, UrlMatchesDetector):
        return f"url matches /{detector.pattern}/"
    if isinstance(detector, AnyOfDetector):
        return "any of: (" + "; ".join(describe_detector(d) for d in detector.any_of) + ")"
    if isinstance(detector, AllOfDetector):
        return "all of: (" + "; ".join(describe_detector(d) for d in detector.all_of) + ")"
    raise ValueError(f"unknown detector kind: {detector!r}")


def describe_observed_state(observation: Observation) -> str:
    heading = observation.page.heading or "(no heading)"
    return f"heading={heading!r} url={observation.page.url!r}"
