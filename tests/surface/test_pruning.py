from __future__ import annotations

from src.surface.pruning import prune_nodes


def test_keeps_interactable_roles(make_node):
    nodes = [
        make_node(role="button", name="Search"),
        make_node(role="textbox", name="Member ID"),
        make_node(role="link", name="Sign Out"),
        make_node(role="checkbox", name="Remember me"),
        make_node(role="radio", name="Savings"),
        make_node(role="combobox", name="Account type"),
        make_node(role="menuitem", name="Export"),
    ]
    assert prune_nodes(nodes) == nodes


def test_keeps_heading(make_node):
    node = make_node(role="heading", name="Account summary")
    assert prune_nodes([node]) == [node]


def test_keeps_dialog_family_roles_even_when_not_visible(make_node):
    for role in ("alert", "alertdialog", "dialog", "status"):
        node = make_node(role=role, name="Disclosure", visible=False)
        assert prune_nodes([node]) == [node]


def test_keeps_non_empty_text_leaf(make_node):
    node = make_node(role="text", name="Internal use only.")
    assert prune_nodes([node]) == [node]


def test_drops_empty_text_leaf(make_node):
    node = make_node(role="text", name="   ")
    assert prune_nodes([node]) == []


def test_drops_invisible_non_dialog_nodes(make_node):
    node = make_node(role="button", name="Search", visible=False)
    assert prune_nodes([node]) == []


def test_drops_unrecognized_presentational_role(make_node):
    node = make_node(role="presentation", name="")
    assert prune_nodes([node]) == []


def test_disabled_interactable_is_kept(make_node):
    node = make_node(role="button", name="Confirm", enabled=False)
    assert prune_nodes([node]) == [node]


def test_mixed_list_filters_in_place_order(make_node):
    keep_button = make_node(ref="n1", role="button", name="Search")
    drop_empty_text = make_node(ref="n2", role="text", name=" ")
    keep_text = make_node(ref="n3", role="text", name="Savings")
    drop_hidden_link = make_node(ref="n4", role="link", name="Hidden", visible=False)
    keep_dialog = make_node(ref="n5", role="dialog", name="Disclosure", visible=False)

    result = prune_nodes([keep_button, drop_empty_text, keep_text, drop_hidden_link, keep_dialog])

    assert result == [keep_button, keep_text, keep_dialog]
