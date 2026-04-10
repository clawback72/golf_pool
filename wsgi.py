"""WSGI entry when running gunicorn from the repo root: ``gunicorn -w 2 -b 127.0.0.1:5000 wsgi:app``."""

import sys
from pathlib import Path

_web = Path(__file__).resolve().parent / "web"
if str(_web) not in sys.path:
    sys.path.insert(0, str(_web))

from golf_site import create_app  # noqa: E402

app = create_app()
