"""The fixed output-transform registry (docs/replay-spec.md §8). Never
arbitrary code in the artifact — a transform is one of these named,
reviewed functions, nothing else.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

TransformError = ValueError

_CURRENCY_RE = re.compile(r"[^0-9.\-]")


def _parse_currency(raw: str) -> Decimal:
    cleaned = _CURRENCY_RE.sub("", raw)
    if not cleaned:
        raise TransformError(f"cannot parse currency from {raw!r}")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise TransformError(f"cannot parse currency from {raw!r}") from exc


def _trim(raw: str) -> str:
    return raw.strip()


def _parse_date(raw: str) -> date:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    raise TransformError(f"cannot parse date from {raw!r}")


def _raw(raw: str) -> str:
    return raw


TRANSFORMS: dict[str, Callable[[str], Any]] = {
    "parse_currency": _parse_currency,
    "trim": _trim,
    "parse_date": _parse_date,
    "raw": _raw,
}


def apply_transform(name: str, raw_value: str) -> Any:
    try:
        transform = TRANSFORMS[name]
    except KeyError:
        raise TransformError(f"unknown transform {name!r}") from None
    return transform(raw_value)
