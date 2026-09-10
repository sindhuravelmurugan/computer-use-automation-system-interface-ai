# Loaded by the Flask CLI before it parses arguments.
# The ${PORT:-5001} expansion is what lets `PORT=5001 flask run` bind correctly.
FLASK_APP=app
FLASK_RUN_PORT=${PORT:-5001}
