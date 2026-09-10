"""In-memory seed data.

Fully fictional. Resets on restart and on ``POST /_test/reset``, so every replay
starts from a known state.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

MemberStatus = Literal["active", "restricted", "closed"]


@dataclass
class SubAccount:
    id: str
    member_id: str
    kind: str
    nickname: str
    opening_deposit: Decimal
    funding_source: str


@dataclass
class Member:
    id: str
    name: str
    status: MemberStatus
    savings: Decimal | None
    sub_accounts: list[SubAccount] = field(default_factory=list)


SUB_ACCOUNT_KINDS: dict[str, str] = {
    "savings_secondary": "Secondary savings",
    "vacation_club": "Vacation club",
    "holiday_club": "Holiday club",
    "youth_savings": "Youth savings",
}

FUNDING_SOURCES: dict[str, str] = {
    "primary_savings": "Transfer from primary savings",
    "branch_cash": "Branch cash deposit",
    "no_funding": "Open unfunded",
}


def _seed() -> dict[str, Member]:
    members = [
        Member("10001", "Ada Thornbury", "active", Decimal("4832.10")),
        Member("10002", "Bertie Mossgrove", "active", Decimal("118.45")),
        Member("10003", "Cyrus Pendlewick", "active", Decimal("92004.00")),
        Member("10004", "Delia Ashcombe", "restricted", None),
        Member("10005", "Emory Vance", "active", Decimal("0.00")),
        Member("10006", "Fenwick Bramble", "closed", Decimal("0.00")),
        Member("10007", "Greta Linnfield", "active", Decimal("1250.75")),
        Member("10008", "Horace Quill", "active", Decimal("7410.20")),
    ]
    by_id = {m.id: m for m in members}
    by_id["10007"].sub_accounts = [
        SubAccount(
            id="SA-10007-01",
            member_id="10007",
            kind="vacation_club",
            nickname="Coast trip",
            opening_deposit=Decimal("50.00"),
            funding_source="primary_savings",
        ),
        SubAccount(
            id="SA-10007-02",
            member_id="10007",
            kind="holiday_club",
            nickname="Winter fund",
            opening_deposit=Decimal("25.00"),
            funding_source="branch_cash",
        ),
    ]
    return by_id


_SEED: dict[str, Member] = _seed()
_members: dict[str, Member] = deepcopy(_SEED)


def reset_data() -> None:
    """Restore the store to its seeded state."""
    global _members
    _members = deepcopy(_SEED)


def get_member(member_id: str) -> Member | None:
    return _members.get(member_id.strip())


def all_members() -> list[Member]:
    return [_members[k] for k in sorted(_members)]


def next_sub_account_id(member: Member) -> str:
    """Deterministic, human-readable, and stable across runs."""
    return f"SA-{member.id}-{len(member.sub_accounts) + 1:02d}"


def add_sub_account(
    member: Member,
    *,
    kind: str,
    nickname: str,
    opening_deposit: Decimal,
    funding_source: str,
) -> SubAccount:
    sub = SubAccount(
        id=next_sub_account_id(member),
        member_id=member.id,
        kind=kind,
        nickname=nickname,
        opening_deposit=opening_deposit,
        funding_source=funding_source,
    )
    member.sub_accounts.append(sub)
    return sub


def get_sub_account(member: Member, sub_id: str) -> SubAccount | None:
    for sub in member.sub_accounts:
        if sub.id == sub_id:
            return sub
    return None


def money(value: Decimal | float | None) -> str:
    """Render a balance the way the legacy app does: commas, two decimals."""
    if value is None:
        return "—"
    return f"{value:,.2f}"
