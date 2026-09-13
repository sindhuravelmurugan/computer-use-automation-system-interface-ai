from __future__ import annotations

import json
from pathlib import Path

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
