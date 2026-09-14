from __future__ import annotations

from tests.policy.conftest import make_config

from src.policy.redaction import REDACTED, Redactor


def test_declared_sensitive_value_is_redacted_in_text():
    redactor = Redactor(make_config().redaction)
    redactor.mark_sensitive("10001")
    assert redactor.redact_text("member_id=10001 found") == f"member_id={REDACTED} found"


def test_non_sensitive_text_is_untouched():
    redactor = Redactor(make_config().redaction)
    assert redactor.redact_text("hello world") == "hello world"


def test_ssn_shaped_string_is_redacted_by_pattern_even_when_undeclared():
    redactor = Redactor(make_config().redaction)
    text = "applicant ssn is 123-45-6789 on file"
    assert "123-45-6789" not in redactor.redact_text(text)
    assert REDACTED in redactor.redact_text(text)


def test_card_shaped_string_is_redacted_by_pattern():
    redactor = Redactor(make_config().redaction)
    text = "card 4111 1111 1111 1111 charged"
    assert "4111 1111 1111 1111" not in redactor.redact_text(text)


def test_balance_shaped_string_does_not_match_any_pattern():
    """Exactly the point of declarations-first: a balance like 4,832.10
    matches no sensitive *pattern*, so pattern redaction alone would miss
    it -- mark_sensitive is what actually catches it."""
    redactor = Redactor(make_config().redaction)
    assert redactor.redact_text("balance is 4,832.10") == "balance is 4,832.10"


def test_key_name_redaction_regardless_of_value_shape():
    redactor = Redactor(make_config().redaction)
    payload = {"password": "hunter2", "username": "agent"}
    redacted = redactor.redact_json(payload)
    assert redacted["password"] == REDACTED
    assert redacted["username"] == "agent"


def test_key_name_matching_is_case_insensitive():
    redactor = Redactor(make_config().redaction)
    payload = {"Password": "hunter2"}
    assert redactor.redact_json(payload)["Password"] == REDACTED


def test_redact_json_walks_nested_structures():
    redactor = Redactor(make_config().redaction)
    redactor.mark_sensitive("10001")
    payload = {
        "step_id": "step_002",
        "action": {"kind": "type", "value": "10001"},
        "history": ["typed 10001 into field"],
    }
    redacted = redactor.redact_json(payload)
    assert redacted["action"]["value"] == REDACTED
    assert redacted["history"][0] == f"typed {REDACTED} into field"
    assert redacted["step_id"] == "step_002"  # untouched, not sensitive


def test_redact_outputs_declared_sensitive_takes_priority_over_pattern_and_key():
    redactor = Redactor(make_config().redaction)
    outputs = {"savings_balance": "4,832.10", "member_name": "Ada Thornbury"}
    redacted = redactor.redact_outputs(outputs, sensitive_names={"savings_balance"})
    assert redacted["savings_balance"] == REDACTED
    assert redacted["member_name"] == "Ada Thornbury"


def test_redact_outputs_still_applies_pattern_net_to_non_declared_fields():
    redactor = Redactor(make_config().redaction)
    outputs = {"notes": "ssn on file: 123-45-6789"}
    redacted = redactor.redact_outputs(outputs, sensitive_names=set())
    assert "123-45-6789" not in redacted["notes"]


def test_mark_sensitive_value_scrubbed_from_every_call_after_it_is_known():
    redactor = Redactor(make_config().redaction)
    before = redactor.redact_text("value is 10001")
    assert "10001" in before  # not yet known sensitive

    redactor.mark_sensitive("10001")
    after = redactor.redact_text("value is 10001")
    assert "10001" not in after


def test_mark_sensitive_ignores_none_and_empty():
    redactor = Redactor(make_config().redaction)
    redactor.mark_sensitive(None)
    redactor.mark_sensitive("")
    assert redactor.redact_text("") == ""
