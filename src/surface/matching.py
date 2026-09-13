"""Pure locator-bundle matching: the strategy-ranking algorithm from
docs/surface-spec.md §3, extracted from ``WebSurface`` so it can run against
any ``list[UINode]`` — live-collected or a frozen ``Observation`` snapshot.

No Playwright import here, deliberately. This is what lets the replay
engine's detector evaluation (``src/replay/detectors.py``) re-use the exact
same targeting algorithm against a past observation without a browser, and
lets it be unit tested as pure data-in/data-out.

DOM-strategy matching is the one exception: it inherently needs to query a
live page, so it's a pluggable ``dom_matcher`` callback. Callers with no live
page (detector evaluation over a frozen snapshot) simply omit it, and DOM
strategies fall through as zero matches — an honest "can't tell from a
snapshot," not a guess.
"""

from __future__ import annotations

from typing import Callable

from src.schema.common import A11yStrategy, DomStrategy, LabelStrategy, SpatialStrategy, TargetStrategy
from src.surface.types import LocatorBundle, Rect, Resolution, UINode

DomMatcher = Callable[[DomStrategy], list[UINode]]


def _vertical_overlap(a: Rect, b: Rect) -> bool:
    return not (a.y + a.height <= b.y or b.y + b.height <= a.y)


def _horizontal_overlap(a: Rect, b: Rect) -> bool:
    return not (a.x + a.width <= b.x or b.x + b.width <= a.x)


def _in_direction(anchor: Rect, other: Rect, direction: str) -> bool:
    if direction == "right":
        return other.x >= anchor.x + anchor.width - 1 and _vertical_overlap(anchor, other)
    if direction == "left":
        return other.x + other.width <= anchor.x + 1 and _vertical_overlap(anchor, other)
    if direction == "down":
        return other.y >= anchor.y + anchor.height - 1 and _horizontal_overlap(anchor, other)
    if direction == "up":
        return other.y + other.height <= anchor.y + 1 and _horizontal_overlap(anchor, other)
    raise ValueError(f"unknown direction {direction!r}")


def _direction_distance(anchor: Rect, other: Rect, direction: str) -> float:
    if direction == "right":
        return other.x - (anchor.x + anchor.width)
    if direction == "left":
        return anchor.x - (other.x + other.width)
    if direction == "down":
        return other.y - (anchor.y + anchor.height)
    if direction == "up":
        return anchor.y - (other.y + other.height)
    raise ValueError(f"unknown direction {direction!r}")


def match_a11y(strategy: A11yStrategy, nodes: list[UINode]) -> list[UINode]:
    return [n for n in nodes if n.role == strategy.role and n.name == strategy.name]


def match_label(strategy: LabelStrategy, nodes: list[UINode]) -> list[UINode]:
    return [n for n in nodes if n.role == strategy.control and n.name == strategy.label_text]


def match_spatial(strategy: SpatialStrategy, nodes: list[UINode]) -> list[UINode]:
    anchors = [n for n in nodes if n.name.strip() == strategy.anchor_text.strip()]
    if len(anchors) != 1:
        # No unique anchor: this strategy cannot resolve. Treated as a
        # zero-match rank so resolve() falls through to the next one.
        return []
    anchor = anchors[0]

    candidates = [
        n
        for n in nodes
        if n is not anchor
        and n.frame_path == anchor.frame_path
        and _in_direction(anchor.bounds, n.bounds, strategy.direction)
    ]
    if not candidates:
        return []

    candidates.sort(key=lambda n: _direction_distance(anchor.bounds, n.bounds, strategy.direction))
    if len(candidates) >= 2:
        d0 = _direction_distance(anchor.bounds, candidates[0].bounds, strategy.direction)
        d1 = _direction_distance(anchor.bounds, candidates[1].bounds, strategy.direction)
        if abs(d0 - d1) < 1.0:
            # Two equally-near candidates: genuinely ambiguous, not a guess
            # between them.
            return candidates[:2]
    return [candidates[0]]


def match_strategy(
    strategy: TargetStrategy, nodes: list[UINode], dom_matcher: DomMatcher | None = None
) -> list[UINode]:
    if isinstance(strategy, A11yStrategy):
        return match_a11y(strategy, nodes)
    if isinstance(strategy, LabelStrategy):
        return match_label(strategy, nodes)
    if isinstance(strategy, SpatialStrategy):
        return match_spatial(strategy, nodes)
    if isinstance(strategy, DomStrategy):
        return dom_matcher(strategy) if dom_matcher is not None else []
    raise ValueError(f"unknown strategy kind {strategy!r}")


def resolve_bundle(
    bundle: LocatorBundle, nodes: list[UINode], dom_matcher: DomMatcher | None = None
) -> Resolution:
    """The ranked-strategy algorithm from docs/surface-spec.md §3: try each
    strategy in order, first unambiguous match wins. A strategy matching more
    than one node is never taken as a guess — it falls through, and if
    nothing else resolves uniquely, the *last* ambiguous rank is reported
    rather than a bare not_found (ambiguous is more informative).
    """
    last_ambiguous: tuple[int, int] | None = None
    for idx, strategy in enumerate(bundle.strategies):
        matches = match_strategy(strategy, nodes, dom_matcher)
        if len(matches) == 1:
            return Resolution(node=matches[0], strategy_index=idx, candidates_found=1, status="resolved")
        if len(matches) > 1:
            last_ambiguous = (idx, len(matches))

    if last_ambiguous is not None:
        idx, count = last_ambiguous
        return Resolution(node=None, strategy_index=idx, candidates_found=count, status="ambiguous")
    return Resolution(node=None, strategy_index=None, candidates_found=0, status="not_found")
