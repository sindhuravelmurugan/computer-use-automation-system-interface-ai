"""Checkpoint inference (docs/discovery-spec.md §6.3): compare the
observation before and after each surviving action and infer a post-
condition from the table. Returns a human-readable note alongside the
detector so the compiler can mark the step -- via its existing `notes`
field, since the schema doesn't add one for this -- as derived rather than
declared, which is exactly what a reviewer needs to know before trusting an
inferred checkpoint that might be too strict.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from src.agent.types import TraceStep
from src.compiler.bundles import build_locator_bundle
from src.schema.common import A11yStrategy, Detector, ElementPresentDetector, Target, UrlMatchesDetector, ValueEqualsDetector
from src.surface.types import Observation, UINode

INFERRED_NOTE_PREFIX = "Checkpoint inferred by compiler"

_SIGNIFICANCE_ORDER = ["dialog", "alertdialog", "alert", "status", "heading", "button", "link", "textbox", "text"]


def infer_checkpoint(trace_step: TraceStep) -> tuple[Detector | None, str | None]:
    kind = trace_step.agent_action.kind
    before, after = trace_step.before, trace_step.after

    if kind == "navigate":
        pattern = re.escape(urlsplit(after.page.url).path)
        return (
            UrlMatchesDetector(pattern=pattern),
            f"{INFERRED_NOTE_PREFIX}: url_matches on the resulting URL path.",
        )

    if kind == "type":
        if trace_step.node is None:
            return None, None
        bundle = build_locator_bundle(trace_step.node)
        action = trace_step.agent_action
        # Must match the compiled step's own `value` field exactly: if this
        # was a declared parameter, the step's value becomes the template
        # placeholder, not the literal discovered during this one run --
        # the checkpoint has to use the same placeholder or it fails on
        # every input except the one this run happened to use.
        expected = ("{{" + action.parameter + "}}") if action.parameter else (action.value or "")
        return (
            ValueEqualsDetector(target=bundle, expected=expected),
            f"{INFERRED_NOTE_PREFIX}: value_equals on the typed field.",
        )

    if kind == "click":
        if after.page.url != before.page.url or after.page.heading != before.page.heading:
            if after.page.heading:
                target = Target(
                    description=f'Heading "{after.page.heading}"',
                    strategies=[A11yStrategy(role="heading", name=after.page.heading)],
                )
                return (
                    ElementPresentDetector(target=target),
                    f"{INFERRED_NOTE_PREFIX}: element_present on the resulting page's heading.",
                )
            return None, None

        new_node = _most_significant_new_node(before, after)
        if new_node is not None:
            target = build_locator_bundle(new_node)
            return (
                ElementPresentDetector(target=target),
                f"{INFERRED_NOTE_PREFIX}: element_present on the most significant node that newly appeared.",
            )
        return None, None

    return None, None


def _most_significant_new_node(before: Observation, after: Observation) -> UINode | None:
    before_keys = {(n.role, n.name, tuple(n.frame_path)) for n in before.nodes}
    new_nodes = [n for n in after.nodes if (n.role, n.name, tuple(n.frame_path)) not in before_keys]
    if not new_nodes:
        return None

    def rank(node: UINode) -> int:
        try:
            return _SIGNIFICANCE_ORDER.index(node.role)
        except ValueError:
            return len(_SIGNIFICANCE_ORDER)

    new_nodes.sort(key=rank)
    return new_nodes[0]
