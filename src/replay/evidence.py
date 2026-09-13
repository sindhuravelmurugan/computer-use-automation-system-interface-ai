"""Evidence writing (docs/replay-spec.md §9, docs/discovery-spec.md §8):
JSONL always, screenshots on the events that matter, and redacted copies of
anything sensitive that gets saved. Redaction happens here, at the logging
boundary -- never at call sites (CLAUDE.md conventions) -- so every event
and every saved file passes through the same substitution instead of each
caller having to remember to.

Shared between the replay path (a single `replay.jsonl`) and discovery
(`trace.jsonl` and `discovery.jsonl` as two independent streams under the
same run) -- one writer, multiple named logs, each with its own seq counter
and fresh-start-on-first-write behavior.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REDACTED = "REDACTED"

DEFAULT_LOG_FILE = "replay.jsonl"


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"not JSON serializable: {value!r}")


def redact(value: Any, sensitive: bool) -> Any:
    return REDACTED if sensitive else value


def redact_outputs(outputs: dict[str, Any], sensitive_names: set[str]) -> dict[str, Any]:
    return {name: redact(value, name in sensitive_names) for name, value in outputs.items()}


class _JsonlStream:
    """One append-only, seq-numbered JSONL file. A run_id (or, for
    discovery, a run_id + log name) is meant to be unique per invocation,
    but if one is ever reused -- a retried run, a leftover dir from a prior
    attempt -- blindly appending would silently corrupt the trail: colliding
    seq numbers, stale events from a run that no longer reflects reality.
    The first write on a fresh instance starts the file over; only writes
    after that append.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._seq = 0
        self._started = False

    def log(self, event: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._started:
            self._path.write_text("", encoding="utf-8")
            self._started = True
        self._seq += 1
        record = {"seq": self._seq, **event}
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=_json_default))
            f.write("\n")


class EvidenceWriter:
    """One instance per run. Creates evidence/{run_id}/ lazily on first
    write -- a run that never has anything worth writing shouldn't leave an
    empty directory behind.
    """

    def __init__(
        self, run_id: str, base_dir: str | Path = "evidence", default_log_file: str = DEFAULT_LOG_FILE
    ) -> None:
        self.run_id = run_id
        self._dir = Path(base_dir) / run_id
        self._streams: dict[str, _JsonlStream] = {}
        self._default_log_file = default_log_file

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _stream(self, file: str) -> _JsonlStream:
        stream = self._streams.get(file)
        if stream is None:
            stream = _JsonlStream(self._dir / file)
            self._streams[file] = stream
        return stream

    def log(self, event: dict[str, Any], *, file: str | None = None) -> None:
        self._ensure_dir()
        self._stream(file or self._default_log_file).log(event)

    def save_screenshot(self, name: str, image_bytes: bytes) -> str:
        self._ensure_dir()
        path = self._dir / f"{name}.png"
        path.write_bytes(image_bytes)
        return str(path)

    def write_json(self, filename: str, payload: dict[str, Any]) -> str:
        self._ensure_dir()
        path = self._dir / filename
        path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
        return str(path)

    def write_result(self, result: Any, sensitive_output_names: set[str]) -> str:
        """Write the redacted result the spec calls "the saved result" —
        sensitive output values never appear here even though the in-memory
        ReplayResult returned to the caller carries the real value.
        """
        payload = json.loads(result.model_dump_json())
        if payload.get("outputs"):
            payload["outputs"] = redact_outputs(payload["outputs"], sensitive_output_names)
        return self.write_json("result.json", payload)

    def write_intervention(self, request: Any) -> str:
        return self.write_json("intervention.json", asdict(request))
