"""Trace recording (docs/discovery-spec.md §8): `trace.jsonl` is the one
place the raw model transcript lives, and it must never carry a sensitive
value. `discovery.jsonl` is the structured, replay-log-shaped stream (§8:
"same format as replay") -- it never carries raw values in the first
place, so it needs no scrubbing beyond what EvidenceWriter already applies.

Redaction itself is `src.policy.redaction.Redactor` (docs/policy-spec.md
§4) -- this module is just the two named streams plus the `mark_sensitive`
passthrough discovery's agent loop calls as soon as a value's sensitivity
becomes known.
"""

from __future__ import annotations

from typing import Any

from src.replay.evidence import EvidenceWriter

TRACE_FILE = "trace.jsonl"
DISCOVERY_FILE = "discovery.jsonl"


class TraceRecorder:
    def __init__(self, evidence: EvidenceWriter) -> None:
        self._evidence = evidence

    def mark_sensitive(self, value: str | None) -> None:
        self._evidence.mark_sensitive(value)

    def log_trace(self, event: dict[str, Any]) -> None:
        self._evidence.log(event, file=TRACE_FILE)

    def log_discovery(self, event: dict[str, Any]) -> None:
        self._evidence.log(event, file=DISCOVERY_FILE)

    def save_screenshot(self, name: str, image_bytes: bytes) -> str:
        return self._evidence.save_screenshot(name, image_bytes)

    def write_intervention(self, request: Any) -> str:
        return self._evidence.write_intervention(request)

    def write_json(self, filename: str, payload: dict[str, Any]) -> str:
        return self._evidence.write_json(filename, payload)
