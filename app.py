"""Entry point for ``flask run``.

``TENANT`` selects the tenant; ``PORT`` is mapped to ``FLASK_RUN_PORT`` in
``.flaskenv`` so the documented run command works as written.
"""

from target_app import create_app

app = create_app()
