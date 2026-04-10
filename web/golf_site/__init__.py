import os
from pathlib import Path

from flask import Flask


def create_app():
    pkg = Path(__file__).resolve().parent
    default_root = pkg.parent.parent
    root = Path(os.environ.get("GOLF_POOL_ROOT", str(default_root))).resolve()

    app = Flask(
        __name__,
        template_folder=str(pkg / "templates"),
        static_folder=str(pkg / "static"),
    )
    app.config["GOLF_POOL_ROOT"] = str(root)

    from golf_site.routes import bp

    app.register_blueprint(bp)

    return app
