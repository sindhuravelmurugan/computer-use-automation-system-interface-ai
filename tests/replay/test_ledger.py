from __future__ import annotations

from src.replay.ledger import AttemptLedger


def test_increment_starts_at_one():
    ledger = AttemptLedger()
    assert ledger.increment("CONFIRMATION_INTERSTITIAL", "step_001") == 1


def test_increment_accumulates_per_key():
    ledger = AttemptLedger()
    ledger.increment("CONFIRMATION_INTERSTITIAL", "step_001")
    assert ledger.increment("CONFIRMATION_INTERSTITIAL", "step_001") == 2
    assert ledger.increment("CONFIRMATION_INTERSTITIAL", "step_001") == 3


def test_keys_are_independent_per_step():
    ledger = AttemptLedger()
    ledger.increment("CONFIRMATION_INTERSTITIAL", "step_001")
    ledger.increment("CONFIRMATION_INTERSTITIAL", "step_001")
    assert ledger.increment("CONFIRMATION_INTERSTITIAL", "step_003") == 1


def test_keys_are_independent_per_code():
    ledger = AttemptLedger()
    ledger.increment("CONFIRMATION_INTERSTITIAL", "step_001")
    assert ledger.increment("SESSION_EXPIRED", "step_001") == 1


def test_count_reads_without_incrementing():
    ledger = AttemptLedger()
    assert ledger.count("CONFIRMATION_INTERSTITIAL", "step_001") == 0
    ledger.increment("CONFIRMATION_INTERSTITIAL", "step_001")
    assert ledger.count("CONFIRMATION_INTERSTITIAL", "step_001") == 1
