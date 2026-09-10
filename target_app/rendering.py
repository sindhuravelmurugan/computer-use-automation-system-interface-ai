"""Page rendering helper.

Every full product page render goes through :func:`render_page`, which is the one
place the disclosure modal can be injected. Fragments that are not top-level page
renders (the search iframe, the 500 page) render directly instead, so they never
consume a modal from the counter.
"""

from __future__ import annotations

from flask import render_template

from .testctl import consume_modal


def render_page(template: str, **context) -> str:
    return render_template(template, modal=consume_modal(), **context)
