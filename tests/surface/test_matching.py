from __future__ import annotations

from src.schema.common import A11yStrategy, DomStrategy, LabelStrategy, SpatialStrategy, Target
from src.surface.matching import resolve_bundle
from src.surface.types import Rect


def bundle(*strategies) -> Target:
    return Target(description="test bundle", strategies=list(strategies))


def test_a11y_match_resolves_at_rank_0(make_node):
    button = make_node(ref="n1", role="button", name="Search")
    other = make_node(ref="n2", role="link", name="Sign Out")
    res = resolve_bundle(bundle(A11yStrategy(role="button", name="Search")), [button, other])
    assert res.status == "resolved"
    assert res.strategy_index == 0
    assert res.node is button


def test_falls_through_zero_matches_to_next_strategy(make_node):
    label = make_node(ref="n1", role="textbox", name="Member ID")
    res = resolve_bundle(
        bundle(
            A11yStrategy(role="button", name="does not exist"),
            A11yStrategy(role="textbox", name="Member ID"),
        ),
        [label],
    )
    assert res.status == "resolved"
    assert res.strategy_index == 1


def test_a11y_ambiguous_match_does_not_guess(make_node):
    a = make_node(ref="n1", role="text", name="Vacation club")
    b = make_node(ref="n2", role="text", name="Vacation club")
    res = resolve_bundle(bundle(A11yStrategy(role="text", name="Vacation club")), [a, b])
    assert res.status == "ambiguous"
    assert res.strategy_index == 0
    assert res.candidates_found == 2
    assert res.node is None


def test_not_found_when_no_strategy_matches(make_node):
    node = make_node(ref="n1", role="button", name="Search")
    res = resolve_bundle(bundle(A11yStrategy(role="button", name="Missing")), [node])
    assert res.status == "not_found"
    assert res.strategy_index is None
    assert res.candidates_found == 0


def test_spatial_finds_node_to_the_right_of_anchor(make_node):
    anchor = make_node(ref="n1", role="text", name="Savings", bounds=Rect(x=0, y=100, width=50, height=13))
    value = make_node(ref="n2", role="text", name="4,832.10", bounds=Rect(x=60, y=100, width=50, height=13))
    unrelated = make_node(ref="n3", role="text", name="Central", bounds=Rect(x=0, y=300, width=50, height=13))
    res = resolve_bundle(bundle(SpatialStrategy(anchor_text="Savings", direction="right")), [anchor, value, unrelated])
    assert res.status == "resolved"
    assert res.node is value


def test_spatial_falls_through_when_anchor_is_not_unique(make_node):
    anchor_a = make_node(ref="n1", role="text", name="Savings", bounds=Rect(x=0, y=0, width=50, height=13))
    anchor_b = make_node(ref="n2", role="text", name="Savings", bounds=Rect(x=0, y=200, width=50, height=13))
    value = make_node(ref="n3", role="text", name="10.00", bounds=Rect(x=60, y=0, width=50, height=13))
    res = resolve_bundle(bundle(SpatialStrategy(anchor_text="Savings", direction="right")), [anchor_a, anchor_b, value])
    assert res.status == "not_found"


def test_spatial_ambiguous_when_two_candidates_equidistant(make_node):
    anchor = make_node(ref="n1", role="text", name="Savings", bounds=Rect(x=0, y=0, width=50, height=13))
    cand_a = make_node(ref="n2", role="text", name="A", bounds=Rect(x=60, y=0, width=20, height=13))
    cand_b = make_node(ref="n3", role="text", name="B", bounds=Rect(x=60, y=50, width=20, height=13))
    # Neither vertically overlaps the anchor's row in a way that ranks one
    # over the other once at the same x -- construct an exact distance tie.
    cand_b_same_row = make_node(ref="n4", role="text", name="B2", bounds=Rect(x=60, y=0, width=20, height=13))
    res = resolve_bundle(
        bundle(SpatialStrategy(anchor_text="Savings", direction="right")),
        [anchor, cand_a, cand_b, cand_b_same_row],
    )
    assert res.status == "ambiguous"


def test_dom_matcher_used_when_provided(make_node):
    node = make_node(ref="dom0", role="text", name="whatever")

    def fake_dom_matcher(strategy: DomStrategy) -> list:
        assert strategy.css == "#foo"
        return [node]

    res = resolve_bundle(bundle(DomStrategy(css="#foo")), [], dom_matcher=fake_dom_matcher)
    assert res.status == "resolved"
    assert res.node is node


def test_dom_strategy_without_matcher_falls_through():
    res = resolve_bundle(bundle(DomStrategy(css="#foo")), [])
    assert res.status == "not_found"


def test_label_strategy_matches_role_and_label_text(make_node):
    field = make_node(ref="n1", role="textbox", name="Nickname")
    res = resolve_bundle(bundle(LabelStrategy(label_text="Nickname", control="textbox")), [field])
    assert res.status == "resolved"
    assert res.node is field
