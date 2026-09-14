from __future__ import annotations

import json
from pathlib import Path

from tests.policy.conftest import make_config

from src.policy.redaction import REDACTED, Redactor
from src.replay.evidence import EvidenceWriter


def test_log_writes_sequential_events(tmp_path):
    writer = EvidenceWriter("run_1", base_dir=tmp_path)
    writer.log({"event": "resolve", "step_id": "step_001"})
    writer.log({"event": "action", "step_id": "step_001"})

    lines = (tmp_path / "run_1" / "replay.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert [r["seq"] for r in records] == [1, 2]


def test_reusing_a_run_id_starts_the_jsonl_fresh(tmp_path):
    """A run_id is meant to be unique, but if a prior run's directory is
    still on disk (a leftover, a retried run), a new EvidenceWriter for the
    same run_id must not append to it -- that would corrupt the trail with
    colliding seq numbers and stale events from a run that no longer
    reflects reality.
    """
    stale = tmp_path / "run_1"
    stale.mkdir(parents=True)
    (stale / "replay.jsonl").write_text(
        json.dumps({"seq": 1, "event": "failure", "step_id": "step_004", "code": "STALE_CODE"}) + "\n",
        encoding="utf-8",
    )

    writer = EvidenceWriter("run_1", base_dir=tmp_path)
    writer.log({"event": "resolve", "step_id": "step_001"})

    lines = (tmp_path / "run_1" / "replay.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert len(records) == 1
    assert records[0]["event"] == "resolve"
    assert not any(r.get("code") == "STALE_CODE" for r in records)


def test_second_writer_instance_for_same_run_id_still_appends_within_itself(tmp_path):
    writer_a = EvidenceWriter("run_1", base_dir=tmp_path)
    writer_a.log({"event": "resolve", "step_id": "step_001"})
    writer_a.log({"event": "action", "step_id": "step_001"})

    lines = (tmp_path / "run_1" / "replay.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_log_redacts_through_the_supplied_redactor(tmp_path):
    redactor = Redactor(make_config().redaction)
    redactor.mark_sensitive("10001")
    writer = EvidenceWriter("run_1", base_dir=tmp_path, redactor=redactor)

    writer.log({"event": "decision", "action": {"kind": "type", "value": "10001"}})

    text = (tmp_path / "run_1" / "replay.jsonl").read_text()
    assert "10001" not in text
    assert REDACTED in text


def test_write_json_redacts_key_names_and_patterns(tmp_path):
    redactor = Redactor(make_config().redaction)
    writer = EvidenceWriter("run_1", base_dir=tmp_path, redactor=redactor)

    writer.write_json("payload.json", {"password": "hunter2", "note": "ssn 123-45-6789 on file"})

    payload = json.loads((tmp_path / "run_1" / "payload.json").read_text())
    assert payload["password"] == REDACTED
    assert "123-45-6789" not in payload["note"]


def test_write_result_redacts_error_fields_that_quote_a_sensitive_value(tmp_path):
    """Regression: a checkpoint failure's error.expected/observed can quote
    the value it compared against -- redaction must not be limited to the
    `outputs` dict."""

    class _FakeErrorResult:
        def model_dump_json(self):
            return json.dumps(
                {
                    "status": "failure",
                    "capability_id": "cap",
                    "capability_version": "1.0.0",
                    "run_id": "run_1",
                    "outputs": None,
                    "outcome": None,
                    "error": {
                        "code": "CHECKPOINT_FAILED",
                        "step_id": "step_002",
                        "expected": "textbox Member ID == '10001'",
                        "observed": "heading='Account summary'",
                        "strategy_used": None,
                        "evidence": None,
                    },
                    "recoveries_applied": [],
                    "duration_ms": 10,
                    "steps_completed": 1,
                }
            )

    redactor = Redactor(make_config().redaction)
    redactor.mark_sensitive("10001")
    writer = EvidenceWriter("run_1", base_dir=tmp_path, redactor=redactor)

    writer.write_result(_FakeErrorResult(), sensitive_output_names=set())

    payload = json.loads((tmp_path / "run_1" / "result.json").read_text())
    assert "10001" not in payload["error"]["expected"]
    assert REDACTED in payload["error"]["expected"]
