"""The attempt ledger: recovery is bounded, never a loop (docs/replay-spec.md
§5). Keyed by (code, step_id) so the same recovery code can be attempted
independently at different steps without one step's budget starving another.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AttemptLedger:
    _counts: dict[tuple[str, str], int] = field(default_factory=dict)

    def increment(self, code: str, step_id: str) -> int:
        """Record an attempt and return the new attempt count (1-indexed)."""
        key = (code, step_id)
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    def count(self, code: str, step_id: str) -> int:
        return self._counts.get((code, step_id), 0)
