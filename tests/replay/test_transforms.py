from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.replay.transforms import TransformError, apply_transform


def test_parse_currency_handles_commas_and_symbol():
    assert apply_transform("parse_currency", "4,832.10") == Decimal("4832.10")
    assert apply_transform("parse_currency", "$1,234.56") == Decimal("1234.56")


def test_parse_currency_handles_negative():
    assert apply_transform("parse_currency", "-92.00") == Decimal("-92.00")


def test_parse_currency_rejects_garbage():
    with pytest.raises(TransformError):
        apply_transform("parse_currency", "not a number")


def test_trim_strips_whitespace():
    assert apply_transform("trim", "  hello  ") == "hello"


def test_parse_date_accepts_iso():
    assert apply_transform("parse_date", "2026-09-09") == date(2026, 9, 9)


def test_parse_date_accepts_us_format():
    assert apply_transform("parse_date", "09/09/2026") == date(2026, 9, 9)


def test_parse_date_rejects_garbage():
    with pytest.raises(TransformError):
        apply_transform("parse_date", "not a date")


def test_raw_passes_through_unchanged():
    assert apply_transform("raw", "4,832.10") == "4,832.10"


def test_unknown_transform_rejected():
    with pytest.raises(TransformError):
        apply_transform("eval_arbitrary_code", "1+1")
