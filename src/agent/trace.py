"""Trace recording (docs/discovery-spec.md §8): `trace.jsonl` is the one
place the raw model transcript lives, and it must never carry a sensitive
value. Declaring a value sensitive doesn't only redact the single event
where it was typed or read -- a typed value keeps reappearing verbatim in
every subsequent observation's node.value until the field changes, so a
value known to be sensitive is scrubbed from every trace entry written
after it becomes known, not just the one that declared it.

`discovery.jsonl` is the structured, replay-log-shaped stream (docs/
discovery-spec.md §8: "same format as replay") -- it never carries raw
values in the first place, so it needs no scrubbing.
"""

from __future__ import annotations

from typing import Any

from src.replay.evidence import EvidenceWriter

TRACE_FILE = "trace.jsonl"
DISCOVERY_FILE = "discovery.jsonl"
REDACTED = "REDACTED"


class TraceRecorder:
    def __init__(self, evidence: EvidenceWriter) -> None:
        self._evidence = evidence
        self._sensitive_values: set[str] = set()

    def mark_sensitive(self, value: str | None) -> None:
        if value:
            self._sensitive_values.add(value)

    def _redact(self, obj: Any) -> Any:
        if isinstance(obj, str):
            redacted = obj
            for value in self._sensitive_values:
                if value and value in redacted:
                    redacted = redacted.replace(value, REDACTED)
            return redacted
        if isinstance(obj, dict):
            return {k: self._redact(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._redact(v) for v in obj]
        return obj

    def log_trace(self, event: dict[str, Any]) -> None:
        self._evidence.log(self._redact(event), file=TRACE_FILE)

    def log_discovery(self, event: dict[str, Any]) -> None:
        self._evidence.log(event, file=DISCOVERY_FILE)

    def save_screenshot(self, name: str, image_bytes: bytes) -> str:
        return self._evidence.save_screenshot(name, image_bytes)

    def write_intervention(self, request: Any) -> str:
        return self._evidence.write_intervention(request)

    def write_json(self, filename: str, payload: dict[str, Any]) -> str:
        return self._evidence.write_json(filename, payload)
