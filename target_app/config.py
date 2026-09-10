"""Tenant configuration.

Two tenants, one codebase. The differing labels are the whole point: a base
capability artifact is recorded against ``meridian`` and re-targeted at
``riverbend`` through a tenant override file.
"""

from __future__ import annotations

from typing import TypedDict


class TenantConfig(TypedDict):
    tenant_id: str
    brand_name: str
    member_id_label: str
    search_button_label: str
    summary_heading: str
    not_found_text: str
    savings_row_label: str
    accent_color: str


TENANTS: dict[str, TenantConfig] = {
    "meridian": {
        "tenant_id": "meridian",
        "brand_name": "Meridian Credit Union",
        "member_id_label": "Member ID",
        "search_button_label": "Search",
        "summary_heading": "Account summary",
        "not_found_text": "No member found",
        "savings_row_label": "Savings",
        "accent_color": "#1b2a55",  # navy
    },
    "riverbend": {
        "tenant_id": "riverbend",
        "brand_name": "Riverbend Federal CU",
        "member_id_label": "Account Number",
        "search_button_label": "Find",
        "summary_heading": "Account overview",
        "not_found_text": "No matching account",
        "savings_row_label": "Savings balance",
        "accent_color": "#1f4d2e",  # forest green
    },
}

DEFAULT_TENANT = "meridian"


def get_tenant(name: str | None) -> TenantConfig:
    """Resolve a tenant by name, falling back to the default."""
    key = (name or DEFAULT_TENANT).strip().lower()
    if key not in TENANTS:
        raise ValueError(
            f"Unknown tenant {key!r}. Known tenants: {', '.join(sorted(TENANTS))}"
        )
    return TENANTS[key]
