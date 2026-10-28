"""Production entry point: gunicorn --bind 0.0.0.0:$PORT wsgi:app."""

from goldilocks.app import create_app

app = create_app()
