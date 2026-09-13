from __future__ import annotations

import pytest

from src.replay.overrides import OverrideError, merge_override
from src.schema.common import A11yStrategy, Target
from src.schema.overrides import OutcomeOverride, StepOverride, SurfaceOverride, TenantOverride


def make_override(**kwargs) -> TenantOverride:
    defaults = dict(
        base_capability_id="member.lookup_savings_balance",
        base_version="1.0.0",
        tenant_id="riverbend",
    )
    defaults.update(kwargs)
    return TenantOverride(**defaults)


def test_merge_retargets_a_step(valid_artifact):
    override = make_override(
        step_overrides={
            "step_002": StepOverride(
                target=Target(
                    description="Account number field",
                    strategies=[A11yStrategy(role="textbox", name="Account Number")],
                )
            )
        }
    )
    merged = merge_override(valid_artifact, override)
    assert merged.steps[1].target.strategies[0].name == "Account Number"
    # base untouched
    assert valid_artifact.steps[1].target.strategies[0].name == "Member ID"


def test_merge_adjusts_an_outcome_detector(valid_artifact):
    override = make_override(
        outcome_overrides={
            "MEMBER_NOT_FOUND": OutcomeOverride(detect={"kind": "text_matches", "pattern": "No matching account"})
        }
    )
    merged = merge_override(valid_artifact, override)
    assert merged.outcomes[0].detect.pattern == "No matching account"
    assert valid_artifact.outcomes[0].detect.pattern == "No member found"


def test_merge_retargets_entry_point(valid_artifact):
    override = make_override(surface=SurfaceOverride(entry_point="http://127.0.0.1:5002/members/search"))
    merged = merge_override(valid_artifact, override)
    assert merged.surface.entry_point == "http://127.0.0.1:5002/members/search"


def test_merge_sets_tenant_id_on_result(valid_artifact):
    merged = merge_override(valid_artifact, make_override())
    assert merged.capability.tenant_id == "riverbend"


def test_merge_rejects_wrong_base_capability_id(valid_artifact):
    override = make_override(base_capability_id="something.else")
    with pytest.raises(OverrideError, match="base_capability_id"):
        merge_override(valid_artifact, override)


def test_merge_rejects_wrong_base_version(valid_artifact):
    override = make_override(base_version="9.9.9")
    with pytest.raises(OverrideError, match="base_version"):
        merge_override(valid_artifact, override)


def test_merge_rejects_unknown_step_id(valid_artifact):
    override = make_override(
        step_overrides={"step_999": StepOverride(target=None)}
    )
    with pytest.raises(OverrideError, match="unknown step_id"):
        merge_override(valid_artifact, override)


def test_merge_rejects_unknown_outcome_code(valid_artifact):
    override = make_override(
        outcome_overrides={"NOT_A_REAL_CODE": OutcomeOverride(detect={"kind": "text_matches", "pattern": "x"})}
    )
    with pytest.raises(OverrideError, match="unknown outcome code"):
        merge_override(valid_artifact, override)
