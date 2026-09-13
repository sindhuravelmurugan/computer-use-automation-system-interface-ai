from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.schema.result import ReplayResult


def _success_result() -> dict:
    return {
        "status": "success",
        "capability_id": "member.lookup_savings_balance",
        "capability_version": "1.2.0",
        "run_id": "run_b7d1e4",
        "outputs": {"savings_balance": "REDACTED"},
        "recoveries_applied": [
            {"code": "CONFIRMATION_INTERSTITIAL", "step_id": "step_003", "attempts": 1}
        ],
        "duration_ms": 4210,
        "steps_completed": 5,
    }


def _failure_result() -> dict:
    return {
        "status": "failure",
        "capability_id": "member.lookup_savings_balance",
        "capability_version": "1.2.0",
        "run_id": "run_b7d1e4",
        "error": {
            "code": "CHECKPOINT_FAILED",
            "step_id": "step_004",
            "expected": "heading 'Account summary' present",
            "observed": "heading 'Permission denied' present",
            "strategy_used": None,
            "evidence": "evidence/run_b7d1e4/step_004.png",
        },
        "duration_ms": 4210,
        "steps_completed": 4,
    }


def _business_outcome_result() -> dict:
    return {
        "status": "business_outcome",
        "capability_id": "member.lookup_savings_balance",
        "capability_version": "1.2.0",
        "run_id": "run_c9a1f0",
        "outcome": {"code": "MEMBER_NOT_FOUND", "returns": {"found": False}},
        "duration_ms": 1800,
        "steps_completed": 3,
    }


@pytest.mark.parametrize(
    "make_result", [_success_result, _failure_result, _business_outcome_result]
)
def test_valid_replay_result_round_trips_through_json(make_result):
    result = ReplayResult.model_validate(make_result())
    reloaded = ReplayResult.model_validate(json.loads(result.model_dump_json()))
    assert reloaded == result


def test_success_status_requires_outputs():
    payload = _success_result()
    payload["outputs"] = None
    payload["outcome"] = {"code": "MEMBER_NOT_FOUND", "returns": {"found": False}}
    with pytest.raises(ValidationError, match="requires 'outputs'"):
        ReplayResult.model_validate(payload)


def test_cannot_populate_both_outputs_and_error():
    payload = _success_result()
    payload["error"] = _failure_result()["error"]
    with pytest.raises(ValidationError, match="exactly one of"):
        ReplayResult.model_validate(payload)


def test_cannot_populate_neither_outputs_outcome_nor_error():
    payload = _success_result()
    del payload["outputs"]
    payload["status"] = "failure"
    with pytest.raises(ValidationError):
        ReplayResult.model_validate(payload)


def test_unknown_status_is_rejected():
    payload = _success_result()
    payload["status"] = "partial"
    with pytest.raises(ValidationError):
        ReplayResult.model_validate(payload)
