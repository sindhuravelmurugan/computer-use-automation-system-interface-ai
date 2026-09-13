"""The per-run context the caller supplies. See docs/replay-spec.md §1."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplayContext:
    run_id: str
    tenant_id: str | None = None
    attended: bool = False
    allow_risky: bool = False
    keep_trace: bool = False
