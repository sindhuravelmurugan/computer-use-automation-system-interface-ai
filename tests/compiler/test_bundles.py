from __future__ import annotations

from src.compiler.bundles import build_locator_bundle


def test_a11y_strategy_emitted_when_name_present(make_node):
    node = make_node(role="button", name="Search", dom_hint=None)
    bundle = build_locator_bundle(node)
    kinds = [s.kind for s in bundle.strategies]
    assert "a11y" in kinds
    a11y = next(s for s in bundle.strategies if s.kind == "a11y")
    assert a11y.role == "button" and a11y.name == "Search"


def test_dom_strategy_emitted_when_dom_hint_present(make_node):
    node = make_node(role="button", name="Search", dom_hint="#ctl00_btnSearch")
    bundle = build_locator_bundle(node)
    dom = next(s for s in bundle.strategies if s.kind == "dom")
    assert dom.css == "#ctl00_btnSearch"


def test_label_strategy_only_for_form_controls(make_node):
    control = make_node(role="textbox", name="Member ID")
    bundle = build_locator_bundle(control)
    assert any(s.kind == "label" for s in bundle.strategies)

    non_control = make_node(role="button", name="Search")
    bundle2 = build_locator_bundle(non_control)
    assert not any(s.kind == "label" for s in bundle2.strategies)


def test_spatial_strategy_only_when_nearby_text_present(make_node):
    with_nearby = make_node(role="text", name="4,832.10", nearby_text=["Savings"])
    bundle = build_locator_bundle(with_nearby)
    spatial = next(s for s in bundle.strategies if s.kind == "spatial")
    assert spatial.anchor_text == "Savings"
    assert spatial.direction == "right"

    without_nearby = make_node(role="text", name="4,832.10", nearby_text=[])
    bundle2 = build_locator_bundle(without_nearby)
    assert not any(s.kind == "spatial" for s in bundle2.strategies)


def test_raises_when_node_supports_no_strategy(make_node):
    import pytest

    node = make_node(role="text", name="", dom_hint=None, nearby_text=[])
    with pytest.raises(ValueError):
        build_locator_bundle(node)


def test_at_least_two_strategies_for_a_typical_interactable(make_node):
    node = make_node(role="button", name="Search", dom_hint="#btn")
    bundle = build_locator_bundle(node)
    assert len(bundle.strategies) >= 2


def test_description_is_non_empty(make_node):
    node = make_node(role="button", name="Search", dom_hint="#btn")
    bundle = build_locator_bundle(node)
    assert bundle.description.strip()
