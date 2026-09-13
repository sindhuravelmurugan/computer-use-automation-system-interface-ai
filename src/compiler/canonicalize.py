"""Route canonicalization (docs/discovery-spec.md §6.4): a concrete path
segment that corresponds to a declared parameter's discovered value becomes
a named placeholder -- `/members/10001` -> `/members/:member_id`. Falls out
naturally once parameters are declared, since the discovered value is
already known; only whole path segments are replaced, never an arbitrary
substring, so a coincidental digit sequence elsewhere in the URL is left
alone.
"""

from __future__ import annotations

import re

from src.schema.artifact import CapabilityArtifact
from src.schema.common import AllOfDetector, AnyOfDetector, Detector, UrlMatchesDetector


def canonicalize_url(url_or_pattern: str, param_values: dict[str, str]) -> str:
    result = url_or_pattern
    for name, value in param_values.items():
        if not value:
            continue
        result = re.sub(rf"(?<=/){re.escape(value)}(?=/|$|\?)", f":{name}", result)
    return result


def _canonicalize_detector(detector: Detector, param_values: dict[str, str]) -> None:
    if isinstance(detector, UrlMatchesDetector):
        detector.pattern = canonicalize_url(detector.pattern, param_values)
    elif isinstance(detector, AnyOfDetector):
        for sub in detector.any_of:
            _canonicalize_detector(sub, param_values)
    elif isinstance(detector, AllOfDetector):
        for sub in detector.all_of:
            _canonicalize_detector(sub, param_values)


def canonicalize_artifact_routes(artifact: CapabilityArtifact, param_values: dict[str, str]) -> None:
    """Mutates `artifact` in place: navigate step values and any inferred
    url_matches checkpoint patterns."""
    if not param_values:
        return
    for step in artifact.steps:
        if step.action == "navigate" and step.value:
            step.value = canonicalize_url(step.value, param_values)
        if step.checkpoint is not None:
            _canonicalize_detector(step.checkpoint, param_values)
