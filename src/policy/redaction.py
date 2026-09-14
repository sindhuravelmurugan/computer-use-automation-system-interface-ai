"""Redaction (docs/policy-spec.md §4): one module, applied at the logging
and serialization boundary, never at call sites. Scattered redaction is
redaction with holes -- a sensitive value leaking into exactly one event
because one call site forgot to scrub is precisely the discovery-session
bug this consolidates away.

Three inputs, in priority order:

1. Schema declarations -- a concrete value marked sensitive by the artifact
   (`mark_sensitive`). Authoritative, because it is declared rather than
   guessed, and it is what lets a value be caught even in fields that were
   never given a suggestive key name (a checkpoint's "expected" string, a
   URL path segment, free text in a trace observation).
2. Config key names -- `always_redact_keys` catches a field literally
   named `password` even when nothing declared its value sensitive.
3. Patterns -- SSN and card-number shapes, as a net for values that appear
   without being declared or named. Shape only, not meaning: a balance
   like "4,832.10" matches no pattern here, which is exactly why (1) has
   to exist and run first.
"""

from __future__ import annotations

import re
from typing import Any

from src.policy.config import RedactionConfig

REDACTED = "REDACTED"


class Redactor:
    def __init__(self, config: RedactionConfig) -> None:
        self._config = config
        self._patterns = [re.compile(pattern) for pattern in config.patterns.values()]
        self._known_sensitive_values: set[str] = set()

    def mark_sensitive(self, value: Any) -> None:
        """Declare a concrete value sensitive once it's known (a resolved
        input, an extracted output). Scrubbed from every redact_* call made
        after this, not just whichever one happens to call mark_sensitive
        first -- a value can reappear (a typed field's node.value on every
        later observation, an error message quoting a checkpoint's expected
        value) long after the step that introduced it.
        """
        if value is None:
            return
        text = str(value)
        if text:
            self._known_sensitive_values.add(text)

    def redact_text(self, text: str) -> str:
        result = text
        for value in self._known_sensitive_values:
            if value and value in result:
                result = result.replace(value, REDACTED)
        for pattern in self._patterns:
            result = pattern.sub(REDACTED, result)
        return result

    def redact_json(self, obj: Any, *, key: str | None = None) -> Any:
        """Recursively redact a JSON-shaped structure -- the general-purpose
        entry point for a log event or a full result payload."""
        if isinstance(obj, dict):
            return {k: self.redact_json(v, key=k) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.redact_json(v, key=key) for v in obj]
        if key is not None and key.lower() in self._config.always_redact_keys:
            return REDACTED
        if isinstance(obj, str):
            return self.redact_text(obj)
        return obj

    def redact_outputs(self, outputs: dict[str, Any], sensitive_names: set[str]) -> dict[str, Any]:
        """`result.json`'s `outputs` mapping: schema-declared sensitivity
        (by output name) is authoritative and checked first; config
        key-names and patterns still apply to the rest as a net.
        """
        result: dict[str, Any] = {}
        for name, value in outputs.items():
            if name in sensitive_names:
                result[name] = REDACTED
            else:
                result[name] = self.redact_json(value, key=name)
        return result
