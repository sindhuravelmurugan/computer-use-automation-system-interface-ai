"""Substitutes ``{{input_name}}`` placeholders in step values and detector
literals with the caller-supplied, pre-validated input values. Deliberately
just string substitution against the declared input names — never `eval`,
never arbitrary expressions.
"""

from __future__ import annotations

import re
from typing import Any

from src.schema.common import AllOfDetector, AnyOfDetector, Detector, TextMatchesDetector, ValueEqualsDetector

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def substitute(text: str, inputs: dict[str, Any]) -> str:
    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in inputs:
            raise KeyError(f"template references undeclared input {name!r}")
        return str(inputs[name])

    return _PLACEHOLDER_RE.sub(_replace, text)


def substitute_detector(detector: Detector, inputs: dict[str, Any]) -> Detector:
    """Deep-copy a detector tree, substituting {{...}} placeholders in the
    literal fields that can carry them (a checkpoint's expected value, a
    text pattern). Targets are never templated -- they come from what the
    surface actually observed, not from caller input.
    """
    copy = detector.model_copy(deep=True)
    if isinstance(copy, ValueEqualsDetector):
        copy.expected = substitute(copy.expected, inputs)
    elif isinstance(copy, TextMatchesDetector):
        copy.pattern = substitute(copy.pattern, inputs)
    elif isinstance(copy, AnyOfDetector):
        copy.any_of = [substitute_detector(d, inputs) for d in copy.any_of]
    elif isinstance(copy, AllOfDetector):
        copy.all_of = [substitute_detector(d, inputs) for d in copy.all_of]
    return copy
