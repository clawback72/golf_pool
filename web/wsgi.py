"""WSGI entry when cwd is `web/`: `gunicorn -w 2 -b 127.0.0.1:5000 wsgi:app`. From repo root use top-level `wsgi.py` instead."""

from golf_site import create_app

app = create_app()
