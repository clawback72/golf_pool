from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def repo_root_from_config(app) -> Path:
    return Path(app.config["GOLF_POOL_ROOT"]).resolve()


def load_json(path: Path):
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def active_tournament_slug(repo: Path) -> str | None:
    g = load_json(repo / "config" / "global.json")
    if not g:
        return None
    slug = g.get("active_tournament")
    return str(slug).strip() if slug else None


def tournament_config_path(repo: Path, slug: str) -> Path:
    return repo / "config" / "tournaments" / f"{slug}.json"


def is_valid_tournament_slug(repo: Path, slug: str) -> bool:
    if not slug or slug.startswith(".") or "/" in slug or "\\" in slug:
        return False
    p = tournament_config_path(repo, slug)
    return p.is_file()


def merge_standings_for_template(snapshot: dict) -> list[dict]:
    if not snapshot:
        return []
    by_name = {d["participant"]: d for d in snapshot.get("detail", [])}
    rows = []
    for s in snapshot.get("standings", []):
        name = s["participant"]
        d = by_name.get(name, {})
        rows.append({
            **s,
            "golfers": d.get("golfers") or [],
        })
    return rows


def format_data_updated_at(ms: int | None) -> str | None:
    if ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        local = dt.astimezone()
        return local.strftime("%m/%d/%Y %I:%M %p").replace(" 0", " ")
    except (ValueError, OSError, TypeError):
        return None


def location_line(ctx: dict | None) -> str:
    if not ctx:
        return ""
    parts = []
    for key in ("city", "state", "country"):
        v = ctx.get(key)
        if v is not None and str(v).strip():
            parts.append(str(v).strip())
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]}, {parts[1]}"
    return f"{parts[0]}, {parts[1]} — {parts[2]}"


def pretty_weather_condition(code: str | None) -> str:
    if not code:
        return ""
    return str(code).replace("_", " ").title()


def round_status_color_class(color: str | None) -> str:
    if not color:
        return "status-pill--neutral"
    c = str(color).strip().upper()
    if c in ("GREY", "GRAY"):
        return "status-pill--gray"
    allowed = ("BLUE", "GREEN", "RED", "YELLOW", "ORANGE", "PURPLE")
    if c in allowed:
        return f"status-pill--{c.lower()}"
    return "status-pill--neutral"


def build_page_context(repo: Path, slug: str) -> dict:
    tcfg = load_json(tournament_config_path(repo, slug)) or {}
    ctx = tcfg.get("tournament_context") or {}
    live = load_json(repo / "config" / "tournaments" / slug / "live_status.json")
    snapshot = load_json(repo / "config" / "tournaments" / slug / "pool_snapshot.json")

    logo_rel = ctx.get("tournament_logo_local") or ""
    beauty_rel = ctx.get("beauty_image_local") or ""

    weather = None
    if live and isinstance(live.get("weather"), dict):
        w = live["weather"]
        weather = {
            "tempF": w.get("tempF"),
            "condition_label": pretty_weather_condition(w.get("condition")),
            "humidity": w.get("humidity"),
            "precipitation": w.get("precipitation"),
            "wind": _wind_line(w),
        }

    return {
        "slug": slug,
        "tournament_name": tcfg.get("tournament_name") or slug,
        "course_name": ctx.get("course_name") or "",
        "location_line": location_line(ctx),
        "logo_filename": Path(logo_rel).name if logo_rel else None,
        "beauty_filename": Path(beauty_rel).name if beauty_rel else None,
        "live": live,
        "weather": weather,
        "snapshot": snapshot,
        "standings_rows": merge_standings_for_template(snapshot) if snapshot else [],
        "missing_picks": (snapshot or {}).get("missing_picks") or {},
        "has_snapshot": bool(snapshot),
        "pga_data_updated_display": format_data_updated_at((live or {}).get("dataUpdatedAt")),
        "round_status_class": round_status_color_class((live or {}).get("roundStatusColor")),
    }


def _wind_line(w: dict) -> str:
    direction = w.get("windDirection") or ""
    speed = w.get("windSpeedMPH")
    direction = str(direction).replace("_", " ").title() if direction else ""
    if speed and direction:
        return f"{direction} {speed} mph"
    if speed:
        return f"{speed} mph"
    return direction or ""


def refresh_meta(repo: Path, slug: str) -> dict:
    live = load_json(repo / "config" / "tournaments" / slug / "live_status.json")
    snapshot = load_json(repo / "config" / "tournaments" / slug / "pool_snapshot.json")
    return {
        "pool_captured_at": (snapshot or {}).get("captured_at"),
        "live_captured_at": (live or {}).get("captured_at"),
        "dataUpdatedAt": (live or {}).get("dataUpdatedAt"),
    }
