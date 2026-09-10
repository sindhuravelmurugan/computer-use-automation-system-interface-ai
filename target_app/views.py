"""Product routes: the read flow, the write flow, and the disclosure modal."""

from __future__ import annotations

import secrets
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, redirect, request, session, url_for

from . import data
from .data import FUNDING_SOURCES, SUB_ACCOUNT_KINDS
from .rendering import render_page

bp = Blueprint("members", __name__)

MAX_NICKNAME = 40


@bp.get("/")
def home():
    return redirect(url_for("members.search"))


# --------------------------------------------------------------------------- #
# Flow A — read flow
# --------------------------------------------------------------------------- #


@bp.get("/members/search")
def search():
    """Shell page. The form itself lives in an iframe, as legacy apps do."""
    return render_page("search.html")


@bp.get("/members/search-frame")
def search_frame():
    """The framed form. Not a top-level page render, so no modal here."""
    from flask import render_template

    return render_template("search_frame.html")


@bp.post("/members/search")
def do_search():
    member_id = (request.form.get("ctl00$ContentMain$txtMemberId") or "").strip()

    if not member_id:
        return render_page("not_found.html", message_key="blank", queried=""), 200

    member = data.get_member(member_id)
    if member is None:
        return (
            render_page("not_found.html", message_key="not_found", queried=member_id),
            200,
        )

    return redirect(url_for("members.detail", member_id=member.id))


@bp.get("/members/<member_id>")
def detail(member_id: str):
    member = data.get_member(member_id)

    if member is None:
        # A missing member is a business result, not an HTTP error.
        return (
            render_page("not_found.html", message_key="not_found", queried=member_id),
            200,
        )

    if member.status == "restricted":
        return render_page("denied.html", member=member), 403

    return render_page("member.html", member=member)


# --------------------------------------------------------------------------- #
# Flow B — write flow: form -> review -> commit
# --------------------------------------------------------------------------- #


def _writable_member(member_id: str):
    """Resolve a member for the write flow, or a rendered refusal response."""
    member = data.get_member(member_id)
    if member is None:
        return None, (
            render_page("not_found.html", message_key="not_found", queried=member_id),
            200,
        )
    if member.status == "restricted":
        return None, (render_page("denied.html", member=member), 403)
    if member.status == "closed":
        return None, (render_page("closed.html", member=member), 200)
    return member, None


def _parse_form(form) -> tuple[dict[str, str], list[str]]:
    kind = (form.get("ctl00$ContentMain$ddlAccountType") or "").strip()
    nickname = (form.get("ctl00$ContentMain$txtNickname") or "").strip()
    deposit_raw = (form.get("ctl00$ContentMain$txtOpeningDeposit") or "").strip()
    funding = (form.get("ctl00$ContentMain$ddlFundingSource") or "").strip()

    errors: list[str] = []
    if kind not in SUB_ACCOUNT_KINDS:
        errors.append("Select a sub-account type.")
    if not nickname:
        errors.append("Enter a nickname.")
    elif len(nickname) > MAX_NICKNAME:
        errors.append(f"Nickname must be {MAX_NICKNAME} characters or fewer.")
    if funding not in FUNDING_SOURCES:
        errors.append("Select a funding source.")

    deposit = deposit_raw.replace(",", "").replace("$", "")
    try:
        amount = Decimal(deposit or "0")
    except InvalidOperation:
        errors.append("Opening deposit must be a number.")
        amount = Decimal("0")
    else:
        if amount < 0:
            errors.append("Opening deposit cannot be negative.")

    values = {
        "kind": kind,
        "nickname": nickname,
        "opening_deposit": f"{amount:.2f}",
        "funding_source": funding,
        "opening_deposit_raw": deposit_raw,
    }
    return values, errors


@bp.get("/members/<member_id>/subaccount/new")
def subaccount_new(member_id: str):
    member, refusal = _writable_member(member_id)
    if refusal is not None:
        return refusal
    return render_page("subaccount_new.html", member=member, values={}, errors=[])


@bp.post("/members/<member_id>/subaccount/review")
def subaccount_review(member_id: str):
    """Stage two. Renders what *would* happen. Commits nothing."""
    member, refusal = _writable_member(member_id)
    if refusal is not None:
        return refusal

    values, errors = _parse_form(request.form)
    if errors:
        return (
            render_page(
                "subaccount_new.html", member=member, values=values, errors=errors
            ),
            200,
        )

    # A single-use token is the proof that this review screen was actually
    # rendered. Without it, /confirm has no way to commit.
    token = secrets.token_urlsafe(16)
    pending = dict(session.get("pending_reviews") or {})
    pending[token] = {**values, "member_id": member.id}
    session["pending_reviews"] = pending

    return render_page(
        "subaccount_review.html", member=member, values=values, review_token=token
    )


@bp.post("/members/<member_id>/subaccount/confirm")
def subaccount_confirm(member_id: str):
    """Stage three. Irreversible. Classified ``risky`` by the policy gate."""
    member, refusal = _writable_member(member_id)
    if refusal is not None:
        return refusal

    token = (request.form.get("ctl00$ContentMain$hidReviewToken") or "").strip()
    pending = dict(session.get("pending_reviews") or {})
    payload = pending.pop(token, None)

    if payload is None or payload.get("member_id") != member.id:
        # No review stage, a tampered token, or a double submit.
        return render_page("stage_error.html", member=member), 400

    session["pending_reviews"] = pending

    sub = data.add_sub_account(
        member,
        kind=payload["kind"],
        nickname=payload["nickname"],
        opening_deposit=Decimal(payload["opening_deposit"]),
        funding_source=payload["funding_source"],
    )
    return redirect(
        url_for("members.subaccount_detail", member_id=member.id, sub_id=sub.id)
    )


@bp.get("/members/<member_id>/subaccount/<sub_id>")
def subaccount_detail(member_id: str, sub_id: str):
    member = data.get_member(member_id)
    if member is None:
        return (
            render_page("not_found.html", message_key="not_found", queried=member_id),
            200,
        )
    if member.status == "restricted":
        return render_page("denied.html", member=member), 403

    sub = data.get_sub_account(member, sub_id)
    if sub is None:
        abort(404)
    return render_page("subaccount_confirmed.html", member=member, sub=sub)


# --------------------------------------------------------------------------- #
# Disclosure modal
# --------------------------------------------------------------------------- #


@bp.post("/acknowledge")
def acknowledge():
    """Dismiss the disclosure modal and return to the page underneath it."""
    target = request.form.get("next") or url_for("members.search")
    # Only ever bounce back to a path on this app.
    if not target.startswith("/") or target.startswith("//"):
        target = url_for("members.search")
    return redirect(target)
