"""Scripted login against this app's mock auth (target_app/auth.py): any
username, a fixed demo password. Every path that might start unauthenticated
-- replay's SESSION_EXPIRED recovery, discovery's bootstrap, the probe pass
-- needs this exact action sequence. Defined once here rather than copied
three times with the chance to drift (which is exactly what nearly happened
while wiring this up).

Deliberately takes an `act` callback rather than a `Surface` directly: each
caller decides whether these actions go through a policy gate (replay and
discovery do; the probe pass, already a fully mechanical re-execution of
the artifact, calls surface.act directly) without this module needing to
know about gates or contexts at all.
"""

from __future__ import annotations

from typing import Callable
from urllib.parse import urlsplit

from src.schema.common import A11yStrategy, Target
from src.surface.types import Action, WaitSpec

ActFn = Callable[[Action], object]

DEFAULT_CREDENTIALS: tuple[str, str] = ("automation", "demo")


def login_url_for(current_url: str) -> str:
    parts = urlsplit(current_url)
    return f"{parts.scheme}://{parts.netloc}/login"


def perform_login(act: ActFn, current_url: str, credentials: tuple[str, str] = DEFAULT_CREDENTIALS) -> None:
    username, password = credentials
    act(Action(kind="navigate", url=login_url_for(current_url), wait=WaitSpec()))
    act(
        Action(
            kind="type",
            target=Target(description="username field", strategies=[A11yStrategy(role="textbox", name="User name")]),
            value=username,
            wait=WaitSpec(),
        )
    )
    act(
        Action(
            kind="type",
            target=Target(description="password field", strategies=[A11yStrategy(role="textbox", name="Password")]),
            value=password,
            wait=WaitSpec(),
        )
    )
    act(
        Action(
            kind="click",
            target=Target(description="sign in button", strategies=[A11yStrategy(role="button", name="Sign In")]),
            wait=WaitSpec(),
        )
    )
