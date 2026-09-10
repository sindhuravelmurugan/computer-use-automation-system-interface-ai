"""Run a tenant directly: ``TENANT=meridian PORT=5001 python -m target_app``."""

from __future__ import annotations

import os

from . import create_app

if __name__ == "__main__":
    create_app().run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5001")),
        debug=False,
        threaded=True,
    )
