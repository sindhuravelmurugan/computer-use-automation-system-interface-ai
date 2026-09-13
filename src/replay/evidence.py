"""Evidence writing (docs/replay-spec.md §9): JSONL always, screenshots on
the events that matter, and a redacted copy of the final result. Redaction
happens here, at the logging boundary -- never at call sites (CLAUDE.md
conventions) -- so every event and every saved file passes through the same
substitution instead of each caller having to remember to.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REDACTED = "REDACTED"


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


class EvidenceWriter:
    """One instance per run. Creates evidence/{run_id}/ lazily on first
    write -- a run that never has anything worth writing (there isn't one,
    since every run at least logs pre-flight, but the principle holds)
    shouldn't leave an empty directory behind.
    """

    def __init__(self, run_id: str, base_dir: str | Path = "evidence") -> None:
        self.run_id = run_id
        self._dir = Path(base_dir) / run_id
        self._seq = 0
        self._jsonl_path = self._dir / "replay.jsonl"
        self._jsonl_started = False

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def log(self, event: dict[str, Any]) -> None:
        self._ensure_dir()
        if not self._jsonl_started:
            # A run_id is meant to be unique per invocation, but if one is
            # ever reused (a retried run, a leftover dir from a prior
            # attempt), appending to whatever is already there would
            # silently corrupt the trail: colliding seq numbers, and stale
            # events from a run that no longer reflects reality. Each
            # EvidenceWriter's first write starts the file fresh; only
            # writes after that append.
            self._jsonl_path.write_text("", encoding="utf-8")
            self._jsonl_started = True
        self._seq += 1
        record = {"seq": self._seq, **event}
        with self._jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=_json_default))
            f.write("\n")

    def save_screenshot(self, name: str, image_bytes: bytes) -> str:
        self._ensure_dir()
        path = self._dir / f"{name}.png"
        path.write_bytes(image_bytes)
        return str(path)

    def write_result(self, result: Any, sensitive_output_names: set[str]) -> str:
        """Write the redacted result the spec calls "the saved result" —
        sensitive output values never appear here even though the in-memory
        ReplayResult returned to the caller carries the real value.
        """
        self._ensure_dir()
        payload = json.loads(result.model_dump_json())
        if payload.get("outputs"):
            payload["outputs"] = redact_outputs(payload["outputs"], sensitive_output_names)
        path = self._dir / "result.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(path)

    def write_intervention(self, request: Any) -> str:
        self._ensure_dir()
        path = self._dir / "intervention.json"
        path.write_text(json.dumps(asdict(request), default=_json_default, indent=2), encoding="utf-8")
        return str(path)
