"""Meridian member servicing console — the target app.

A deliberately legacy-styled Flask app that exists to be automated. Ugly, not
inaccessible: every input keeps a real ``<label for=...>``.
"""

from __future__ import annotations

import os
import secrets
import time

from flask import Flask, redirect, render_template, request, session, url_for

from . import auth, testctl, views
from .config import get_tenant
from .data import FUNDING_SOURCES, SUB_ACCOUNT_KINDS, money

DEFAULT_SESSION_TTL_SECONDS = 1800

# Paths that never require a session and never get a latency/error injection.
_UNAUTHENTICATED_PREFIXES = ("/login", "/logout", "/static", "/_test", "/acknowledge")


def create_app() -> Flask:
    app = Flask(__name__)
    tenant = get_tenant(os.environ.get("TENANT"))

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        TENANT=tenant,
        SESSION_TTL_SECONDS=int(
            os.environ.get("SESSION_TTL_SECONDS", DEFAULT_SESSION_TTL_SECONDS)
        ),
    )

    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)
    app.register_blueprint(testctl.bp)

    @app.before_request
    def inject_failures():
        """Armed failures fire before anything else looks at the request."""
        if request.path.startswith(("/_test", "/static")):
            return None
        if testctl.consume_error_on_next():
            return render_template("error.html"), 500
        delay_ms = testctl.consume_latency_ms()
        if delay_ms:
            time.sleep(delay_ms / 1000.0)
        return None

    @app.before_request
    def require_session():
        if request.path.startswith(_UNAUTHENTICATED_PREFIXES):
            return None
        if auth.session_is_valid():
            return None
        # Only a session that existed and lapsed is "expired" — a request that
        # never had one is just unauthenticated.
        had_session = bool(session.get("user"))
        if had_session:
            return redirect(url_for("auth.login", expired=1))
        return redirect(url_for("auth.login"))

    @app.context_processor
    def inject_globals():
        return {
            "tenant": app.config["TENANT"],
            "money": money,
            "account_kinds": SUB_ACCOUNT_KINDS,
            "funding_sources": FUNDING_SOURCES,
            "current_path": request.full_path.rstrip("?"),
        }

    @app.errorhandler(404)
    def not_found(_err):
        return render_template("http_404.html"), 404

    @app.errorhandler(500)
    def server_error(_err):
        return render_template("error.html"), 500

    return app
