"""Mock authentication.

Any username, one shared demo password, a session with a TTL. There are no real
credentials in this repo and nothing here is a security control — it exists so
the automation has a login screen to recover to when a session expires.
"""

from __future__ import annotations

import time

from flask import Blueprint, current_app, redirect, request, session, url_for

from .rendering import render_page
from .testctl import session_epoch

DEMO_PASSWORD = "demo"  # noqa: S105 - fixture credential for a simulated app

bp = Blueprint("auth", __name__)


def start_session(username: str) -> None:
    session.clear()
    session["user"] = username
    session["issued_at"] = time.time()
    session["epoch"] = session_epoch()


def session_is_valid() -> bool:
    if not session.get("user"):
        return False
    if session.get("epoch") != session_epoch():
        return False
    ttl = current_app.config["SESSION_TTL_SECONDS"]
    return (time.time() - float(session.get("issued_at", 0))) <= ttl


@bp.get("/login")
def login():
    expired = request.args.get("expired") == "1"
    return render_page("login.html", expired=expired, error=None)


@bp.post("/login")
def do_login():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    if not username or password != DEMO_PASSWORD:
        return (
            render_page(
                "login.html",
                expired=False,
                error="Sign-in failed. Check the user name and password.",
            ),
            200,
        )

    start_session(username)
    return redirect(url_for("members.search"))


@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
