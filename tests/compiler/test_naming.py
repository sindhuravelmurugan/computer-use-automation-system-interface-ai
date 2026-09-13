from __future__ import annotations

from src.compiler.naming import derive_capability_id


def test_look_up_member_goal_matches_project_convention():
    result = derive_capability_id("Look up member 10001 and read their savings balance", "savings_balance")
    assert result == "member.lookup_savings_balance"


def test_falls_back_to_leading_words_when_no_primary_output():
    result = derive_capability_id("Open a new sub-account for member 10007", None)
    assert result.startswith("open.") or result.startswith("a.")


def test_unknown_verb_falls_back_to_run():
    result = derive_capability_id("Do something unexpected", None)
    assert result.split(".")[1].startswith("run_")
