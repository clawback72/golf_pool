from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    send_from_directory,
)
from werkzeug.utils import secure_filename

from golf_site import data as data_mod

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    repo = data_mod.repo_root_from_config(current_app)
    slug = data_mod.active_tournament_slug(repo)
    if not slug:
        return render_template(
            "pool.html",
            error="No active tournament set in config/global.json (active_tournament).",
            page=None,
            initial_meta={},
        ), 503
    if not data_mod.is_valid_tournament_slug(repo, slug):
        return render_template(
            "pool.html",
            error=f"Active tournament config not found: {slug}",
            page=None,
            initial_meta={},
        ), 503

    page = data_mod.build_page_context(repo, slug)
    initial_meta = data_mod.refresh_meta(repo, slug)
    return render_template("pool.html", error=None, page=page, initial_meta=initial_meta)


@bp.route("/api/refresh-meta")
def api_refresh_meta():
    repo = data_mod.repo_root_from_config(current_app)
    slug = data_mod.active_tournament_slug(repo)
    if not slug or not data_mod.is_valid_tournament_slug(repo, slug):
        return jsonify({"pool_captured_at": None, "live_captured_at": None, "dataUpdatedAt": None})
    return jsonify(data_mod.refresh_meta(repo, slug))


@bp.route("/assets/<slug>/<filename>")
def tournament_asset(slug, filename):
    repo = data_mod.repo_root_from_config(current_app)
    if not data_mod.is_valid_tournament_slug(repo, slug):
        abort(404)
    if "/" in filename or "\\" in filename or filename.startswith("."):
        abort(404)
    safe = secure_filename(filename)
    if not safe or safe != filename:
        abort(404)
    assets_dir = repo / "config" / "tournaments" / slug / "assets"
    if not assets_dir.is_dir():
        abort(404)
    target = (assets_dir / safe).resolve()
    try:
        target.relative_to(assets_dir.resolve())
    except ValueError:
        abort(404)
    if not target.is_file():
        abort(404)
    return send_from_directory(str(assets_dir), safe)
