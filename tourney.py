import argparse
import copy
import json
import os
import time
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytz
import requests
from bs4 import BeautifulSoup


DEFAULT_TOURNAMENT = "open_2025"
DEFAULT_LEADERBOARD_URL = "https://www.pgatour.com/leaderboard"
# First letter of PGA tournament id selects tour; each tour has its own leaderboard base URL.
TOUR_LEADERBOARD_URL = {
    "R": "https://www.pgatour.com/leaderboard",
    "S": "https://www.pgatour.com/pgatour-champions/leaderboard",
    "H": "https://www.pgatour.com/korn-ferry-tour/leaderboard",
    "Y": "https://www.pgatour.com/americas/leaderboard",
}
# When two R* events run (e.g. major week), the non-field event may use this page instead of the primary leaderboard.
PGA_TOUR_SECONDARY_LEADERBOARD_URL = "https://www.pgatour.com/pgatour/leaderboard"

TOUR_PREFIX_LABEL = {
    "R": "PGA Tour (tournament id starts with R)",
    "S": "PGA Tour Champions (S)",
    "H": "Korn Ferry Tour (H)",
    "Y": "PGA Tour Americas (Y)",
}

DEFAULT_POOL_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1uMtqS8_NfIQ1N87i8ngO5-dnYaeCeVbrACaLuLDP7pc/edit?gid=0#gid=0"
)
CONFIG_DIR = Path(__file__).resolve().parent / "config"
GLOBAL_CONFIG_PATH = CONFIG_DIR / "global.json"
TOURNAMENTS_DIR = CONFIG_DIR / "tournaments"

BACK_OPTION_LABEL = "← Back (previous menu)"


def load_json_file(path):
    with open(path, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def save_json_file(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, indent=2)


def slugify(text):
    chars = []
    for ch in text.lower().strip():
        if ch.isalnum():
            chars.append(ch)
        elif ch in (" ", "-", "_", "/"):
            chars.append("_")
    slug = "".join(chars)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_") or "tournament"


def deep_merge(base, override):
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_tournament_id(cli_tournament):
    if cli_tournament:
        return cli_tournament
    if os.getenv("TOURNAMENT"):
        return os.getenv("TOURNAMENT")
    if GLOBAL_CONFIG_PATH.exists():
        global_config = load_json_file(GLOBAL_CONFIG_PATH)
        active = global_config.get("active_tournament")
        if active:
            return active
    return DEFAULT_TOURNAMENT


def load_config(tournament_id):
    if not GLOBAL_CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing global config: {GLOBAL_CONFIG_PATH}")

    tournament_path = TOURNAMENTS_DIR / f"{tournament_id}.json"
    if not tournament_path.exists():
        raise FileNotFoundError(f"Missing tournament config: {tournament_path}")

    global_config = load_json_file(GLOBAL_CONFIG_PATH)
    tournament_config = load_json_file(tournament_path)
    config = deep_merge(global_config, tournament_config)
    config["selected_tournament_id"] = tournament_id
    return config


def validate_config(config):
    required_keys = ["tournament_name", "source", "pool_enabled", "participants", "run_window", "output"]
    for key in required_keys:
        if key not in config:
            raise ValueError(f"Config is missing required key: {key}")

    if not isinstance(config["source"], dict):
        raise ValueError("Config source must be a dictionary")

    if not isinstance(config["participants"], dict):
        raise ValueError("Config participants must be a dictionary")


def fetch_site_json(url):
    response = requests.get(url, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to retrieve data: {response.status_code}")
    soup = BeautifulSoup(response.content, "html.parser")
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script_tag:
        raise RuntimeError("JSON data not found on the page.")
    return json.loads(script_tag.string)


def extract_tournament_candidates(json_data):
    # Schedule list: props.pageProps.dehydratedState.queries[*].state.data -> [tournament objects]
    # Include in-progress and future events; exclude completed / cancelled.
    exclude_statuses = {"COMPLETED", "CANCELLED", "CANCELED"}
    candidates = {}
    queries = json_data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])

    for query in queries:
        data = query.get("state", {}).get("data")
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            tournament_name = item.get("tournamentName")
            tournament_status = item.get("tournamentStatus")
            tournament_id = item.get("id")
            if not isinstance(tournament_name, str) or not tournament_name.strip():
                continue
            if tournament_status in exclude_statuses:
                continue

            slug = slugify(str(tournament_id or tournament_name))
            display_date = item.get("displayDate", "")
            label = tournament_name.strip()
            if display_date:
                label = f"{label} ({display_date})"

            candidates[slug] = {
                "kind": "pga",
                "slug": slug,
                "name": label,
                "pga_event_id": str(tournament_id) if tournament_id else None,
            }

    ordered = sorted(candidates.values(), key=lambda x: x["name"].lower())
    if not ordered:
        ordered = [{
            "kind": "pga",
            "slug": DEFAULT_TOURNAMENT,
            "name": "PGA Tour Leaderboard (fallback)",
            "pga_event_id": None,
        }]
    return ordered[:40]


def list_local_tournaments():
    if not TOURNAMENTS_DIR.exists():
        return []
    local = []
    for path in sorted(TOURNAMENTS_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        cfg = load_json_file(path)
        local.append({
            "slug": path.stem,
            "name": cfg.get("tournament_name", path.stem),
            "path": path,
            "config": cfg,
        })
    return local


def prompt_choice(prompt_text, options, allow_blank=False, allow_back=False):
    opts = list(options)
    if allow_back:
        opts = opts + [BACK_OPTION_LABEL]
    print(f"\n{prompt_text}")
    for idx, option in enumerate(opts, start=1):
        print(f"  {idx}) {option}")
    while True:
        value = input("Choose number: ").strip()
        if allow_blank and value == "":
            return None
        if value.isdigit() and 1 <= int(value) <= len(opts):
            choice_idx = int(value) - 1
            if allow_back and choice_idx == len(options):
                return None
            return choice_idx
        print("Invalid selection. Try again.")


def prompt_yes_no(prompt_text, default=True, allow_back=False):
    back_hint = " b=back" if allow_back else ""
    suffix = "[Y/n]" if default else "[y/N]"
    value = input(f"{prompt_text} {suffix}{back_hint}: ").strip().lower()
    if allow_back and value in ("b", "back"):
        return None
    if value == "":
        return default
    return value in ("y", "yes")


def prompt_line(prompt_text, default=None, allow_back=False):
    hint = " [b=back]" if allow_back else ""
    raw = input(f"{prompt_text}{hint}: ").strip()
    if allow_back and raw.lower() in ("b", "back"):
        return None
    if raw == "" and default is not None:
        return default
    return raw if raw else None


def default_leaderboard_url_for_event_id(pga_event_id):
    if not pga_event_id:
        return DEFAULT_LEADERBOARD_URL
    letter = str(pga_event_id)[0].upper()
    return TOUR_LEADERBOARD_URL.get(letter, DEFAULT_LEADERBOARD_URL)


def tour_label_for_event_id(pga_event_id):
    if not pga_event_id:
        return "No event id (using primary PGA leaderboard page)"
    letter = str(pga_event_id)[0].upper()
    return TOUR_PREFIX_LABEL.get(letter, f"Unknown prefix {letter!r} — using primary PGA leaderboard as default")


def resolve_leaderboard_url(source_dict):
    """Pick leaderboard page URL: tournament-specific wins unless it's only the global PGA default."""
    src = source_dict or {}
    explicit = (src.get("leaderboard_url") or "").strip()
    derived = default_leaderboard_url_for_event_id(src.get("pga_event_id"))
    pid = src.get("pga_event_id")
    letter = str(pid)[0].upper() if pid else ""

    def norm(u):
        return u.rstrip("/").lower()

    if not explicit:
        return derived

    # deep_merge often pulls source.leaderboard_url from global.json (PGA primary). That must not
    # override the Korn Ferry / Champions / Americas pages implied by H/S/Y event ids.
    if letter in ("S", "H", "Y") and norm(explicit) == norm(DEFAULT_LEADERBOARD_URL):
        return derived

    return explicit


def _short_url(u, max_len=52):
    u = str(u or "")
    return u if len(u) <= max_len else u[: max_len - 3] + "..."


def edit_leaderboard_url_menu(cfg):
    """Confirm or set leaderboard page URL from tour defaults, dual-R option, or manual entry."""
    src = cfg.setdefault("source", {})
    pid = src.get("pga_event_id")
    tour_label = tour_label_for_event_id(pid)
    suggested_primary = default_leaderboard_url_for_event_id(pid)
    letter = str(pid)[0].upper() if pid else ""

    while True:
        current = resolve_leaderboard_url(src)
        print(f"\nLeaderboard page (HTML source for __NEXT_DATA__)")
        print(f"  Event id: {pid or '(none)'}")
        print(f"  Tour: {tour_label}")
        print(f"  Active URL: {current}")

        if letter == "R":
            idx = prompt_choice(
                "Choose leaderboard page URL:",
                [
                    f"Primary PGA Tour (typical / majors field): {_short_url(TOUR_LEADERBOARD_URL['R'])}",
                    f"Secondary PGA Tour (other R event same week): {_short_url(PGA_TOUR_SECONDARY_LEADERBOARD_URL)}",
                    "Enter a custom URL",
                    "← Back (tournament editor)",
                ],
                allow_back=False,
            )
            if idx == 0:
                src["leaderboard_url"] = TOUR_LEADERBOARD_URL["R"]
            elif idx == 1:
                src["leaderboard_url"] = PGA_TOUR_SECONDARY_LEADERBOARD_URL
            elif idx == 2:
                line = prompt_line(
                    "Full leaderboard page URL",
                    default=src.get("leaderboard_url") or suggested_primary,
                    allow_back=True,
                )
                if line is None:
                    continue
                src["leaderboard_url"] = line.strip()
            else:
                return
            print(f"Set to: {src['leaderboard_url']}")
            return

        idx = prompt_choice(
            "Choose leaderboard page URL:",
            [
                f"Use suggested URL for this id: {_short_url(suggested_primary)}",
                "Enter a custom URL",
                "← Back (tournament editor)",
            ],
            allow_back=False,
        )
        if idx == 0:
            src["leaderboard_url"] = suggested_primary
            print(f"Set to: {src['leaderboard_url']}")
            return
        if idx == 1:
            line = prompt_line(
                "Full leaderboard page URL",
                default=src.get("leaderboard_url") or suggested_primary,
                allow_back=True,
            )
            if line is None:
                continue
            src["leaderboard_url"] = line.strip()
            print(f"Set to: {src['leaderboard_url']}")
            return
        return


def _event_ids_match(wanted, got):
    if wanted is None or got is None:
        return False
    return str(wanted).strip().upper() == str(got).strip().upper()


def _leaderboard_v3_players(state_data):
    """Players array from a LeaderboardV3 payload (not odds/other queries with a players list)."""
    if not isinstance(state_data, dict):
        return None
    if state_data.get("__typename") != "LeaderboardV3":
        return None
    players = state_data.get("players")
    if isinstance(players, list) and players:
        return players
    return None


def find_leaderboard_players(json_data, pga_event_id=None):
    """Match dehydrated queries: real field uses queryKey ['leaderboard', {leaderboardId}], not tournament id."""
    queries = json_data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
    fallback = None

    if pga_event_id:
        for query in queries:
            qk = query.get("queryKey")
            if not isinstance(qk, list) or len(qk) < 2:
                continue
            key_name = qk[0]
            arg = qk[1]
            if not isinstance(arg, dict):
                continue
            state_data = query.get("state", {}).get("data")
            matched = False
            if key_name == "leaderboard" and _event_ids_match(pga_event_id, arg.get("leaderboardId")):
                matched = True
            elif key_name == "tournament" and _event_ids_match(pga_event_id, arg.get("id")):
                matched = True
            if matched:
                players = _leaderboard_v3_players(state_data)
                if players is not None:
                    return players

        for query in queries:
            state_data = query.get("state", {}).get("data")
            if not isinstance(state_data, dict):
                continue
            tid = state_data.get("tournamentId") or state_data.get("id")
            if not _event_ids_match(pga_event_id, tid):
                continue
            players = _leaderboard_v3_players(state_data)
            if players is not None:
                return players

        print(
            f"Warning: No leaderboard query matched pga_event_id={pga_event_id!r}; "
            "using first LeaderboardV3 on page (wrong tour or typo in id?)."
        )

    for query in queries:
        state_data = query.get("state", {}).get("data")
        players = _leaderboard_v3_players(state_data)
        if players is not None:
            if fallback is None:
                fallback = players
        elif isinstance(state_data, list):
            for item in state_data:
                if not isinstance(item, dict):
                    continue
                players = _leaderboard_v3_players(item)
                if players is not None:
                    if fallback is None:
                        fallback = players
                    break
    return fallback


def load_golfer_lookup(source_dict):
    try:
        now = datetime.now()
        backup = {"enabled": False, "directory": ""}
        src = source_dict or {}
        df = get_data(
            now,
            backup,
            pga_event_id=src.get("pga_event_id"),
            leaderboard_url=resolve_leaderboard_url(src),
        )
        if df.empty:
            return []
        lookup = []
        for _, row in df.iterrows():
            player_id = row.get("PlayerID")
            name = row.get("Name")
            if not player_id or not name:
                continue
            lookup.append({
                "PlayerID": str(player_id),
                "Name": str(name),
                "Position": str(row.get("Position", "")),
                "Score": str(row.get("Score", "")),
            })
        return lookup
    except Exception as exc:
        print(f"Unable to load golfer lookup from leaderboard: {exc}")
        return []


def print_all_golfers(lookup):
    if not lookup:
        print("No golfers to list.")
        return
    sorted_g = sorted(lookup, key=lambda g: g["Name"].lower())
    print(f"\nAll golfers in field ({len(sorted_g)}):")
    for g in sorted_g:
        print(f"  {g['Name']} | ID: {g['PlayerID']}")


def search_golfers(lookup, term, limit=20):
    if not term:
        return lookup[:limit]
    term_l = term.lower()
    starts = [g for g in lookup if g["Name"].lower().startswith(term_l)]
    contains = [g for g in lookup if term_l in g["Name"].lower() and g not in starts]
    id_matches = [g for g in lookup if term_l in g["PlayerID"] and g not in starts and g not in contains]
    return (starts + contains + id_matches)[:limit]


def print_golfer_matches(matches):
    if not matches:
        print("No golfers matched that search.")
        return
    print("\nMatching golfers:")
    for golfer in matches:
        print(f"  {golfer['Name']} | ID: {golfer['PlayerID']} | Pos: {golfer['Position']} | Score: {golfer['Score']}")


def print_participant_summary(participants, golfer_lookup):
    print("\nParticipant picks summary:")
    if not participants:
        print("  (none)")
        return
    name_by_id = {g["PlayerID"]: g["Name"] for g in golfer_lookup}
    for name, picks in participants.items():
        pretty = [f"{pid} ({name_by_id.get(pid, 'unknown')})" for pid in picks]
        print(f"  - {name}: {', '.join(pretty) if pretty else '(no picks)'}")


def _participant_name_exists(participants, name):
    nl = name.strip().lower()
    return any(k.lower() == nl for k in participants)


def _find_duplicate_team_owner(participants, current_name, picks):
    team = frozenset(picks)
    for other_name, other_picks in participants.items():
        if other_name == current_name:
            continue
        if frozenset(other_picks) == team:
            return other_name
    return None


def interactive_assign_golfers(participants, golfer_lookup):
    if not golfer_lookup:
        print("Golfer lookup is unavailable; cannot run interactive assignment.")
        return

    existing_names = sorted(participants.keys())
    print("\nInteractive assignment")
    if existing_names:
        idx = prompt_choice(
            "Assign picks for:",
            existing_names + ["New participant"],
            allow_back=True,
        )
        if idx is None:
            return
        if idx == len(existing_names):
            while True:
                participant_name = prompt_line("New participant name", allow_back=True)
                if participant_name is None:
                    return
                participant_name = participant_name.strip()
                if not participant_name:
                    print("Name cannot be empty.")
                    continue
                if _participant_name_exists(participants, participant_name):
                    print(f"A participant named '{participant_name}' already exists (case-insensitive). Choose another name.")
                    continue
                break
        else:
            participant_name = existing_names[idx]
    else:
        while True:
            participant_name = prompt_line("Participant name", allow_back=True)
            if participant_name is None:
                return
            participant_name = participant_name.strip()
            if not participant_name:
                print("Name cannot be empty.")
                continue
            if _participant_name_exists(participants, participant_name):
                print(f"A participant named '{participant_name}' already exists.")
                continue
            break

    picks = list(participants.get(participant_name, []))
    while True:
        print(f"\nEditing picks for {participant_name}")
        if picks:
            print("Current picks:")
            name_by_id = {g["PlayerID"]: g["Name"] for g in golfer_lookup}
            for i, pid in enumerate(picks, start=1):
                print(f"  {i}) {pid} ({name_by_id.get(pid, 'unknown')})")
        else:
            print("Current picks: (none)")

        action = prompt_choice(
            "Choose action:",
            ["Add golfer from search", "Remove golfer", "Finish participant"],
            allow_back=True,
        )
        if action is None:
            return
        if action == 0:
            term = prompt_line("Search golfer name or ID", default="", allow_back=True)
            if term is None:
                continue
            matches = search_golfers(golfer_lookup, term, limit=15)
            print_golfer_matches(matches)
            if not matches:
                continue
            pick_idx = prompt_choice(
                "Add which golfer?",
                [f"{m['Name']} ({m['PlayerID']})" for m in matches],
                allow_back=True,
            )
            if pick_idx is None:
                continue
            player_id = matches[pick_idx]["PlayerID"]
            if player_id in picks:
                print("That golfer is already in this participant's picks.")
            else:
                picks.append(player_id)
        elif action == 1:
            if not picks:
                print("No picks to remove.")
                continue
            rm_idx = prompt_choice("Remove which pick?", [f"{pid}" for pid in picks], allow_back=True)
            if rm_idx is None:
                continue
            picks.pop(rm_idx)
        else:
            dup_owner = _find_duplicate_team_owner(participants, participant_name, picks)
            if dup_owner:
                print(
                    f"This set of golfers matches another participant ({dup_owner}). "
                    "Add or remove picks until your team is unique, then finish again."
                )
                continue
            participants[participant_name] = picks
            return


def set_active_tournament(slug):
    global_config = load_json_file(GLOBAL_CONFIG_PATH) if GLOBAL_CONFIG_PATH.exists() else {}
    global_config["active_tournament"] = slug
    save_json_file(GLOBAL_CONFIG_PATH, global_config)
    print(f"Active tournament set to: {slug}")


def pick_tournament_entry(site_json, allow_back=True):
    """PGA active/upcoming events from site JSON plus local configs not already listed."""
    scraped = extract_tournament_candidates(site_json)
    seen_slugs = {c["slug"] for c in scraped}
    entries = []
    for c in scraped:
        entries.append(dict(c))

    for loc in list_local_tournaments():
        if loc["slug"] in seen_slugs:
            continue
        entries.append({
            "kind": "local",
            "slug": loc["slug"],
            "name": loc["name"],
            "path": loc["path"],
            "pga_event_id": loc["config"].get("source", {}).get("pga_event_id"),
        })

    if not entries:
        print("No tournaments available from PGA site or local config.")
        return None

    labels = []
    for e in entries:
        if e["kind"] == "pga":
            labels.append(f"[PGA] {e['name']}")
        else:
            labels.append(f"[Local] {e['name']} ({e['slug']})")

    idx = prompt_choice("Select tournament to configure:", labels, allow_back=allow_back)
    if idx is None:
        return None
    return entries[idx]


def prompt_for_participants(existing_participants, source_dict):
    participants = dict(existing_participants or {})
    source_dict = dict(source_dict or {})
    golfer_lookup = load_golfer_lookup(source_dict)
    if golfer_lookup:
        print(f"Loaded {len(golfer_lookup)} golfers for search helper.")
    else:
        peid = source_dict.get("pga_event_id") or "(not set — using first leaderboard in page)"
        print(f"Golfer lookup unavailable (pga_event_id={peid}). Try Refresh after saving event id.")
    while True:
        print("\nParticipant editor")
        print_participant_summary(participants, golfer_lookup)
        action = prompt_choice(
            "Choose action:",
            [
                "Refresh golfer lookup",
                "List all golfers (name + ID)",
                "Search golfers by name/ID",
                "Interactive assign golfers to participant",
                "Remove participant",
                "Finish editing",
                "← Back (tournament editor)",
            ],
            allow_back=False,
        )
        if action == 0:
            golfer_lookup = load_golfer_lookup(source_dict)
            if golfer_lookup:
                print(f"Loaded {len(golfer_lookup)} golfers for search helper.")
            else:
                peid = source_dict.get("pga_event_id") or "(not set)"
                print(f"Golfer lookup still unavailable (pga_event_id={peid}).")
        elif action == 1:
            if not golfer_lookup:
                print("Golfer lookup is unavailable; refresh or fix tournament event id.")
                continue
            print_all_golfers(golfer_lookup)
        elif action == 2:
            if not golfer_lookup:
                print("Golfer lookup is unavailable for this tournament.")
                continue
            search_term = prompt_line("Search golfer name or ID (blank shows first matches)", default="", allow_back=True)
            if search_term is None:
                continue
            print_golfer_matches(search_golfers(golfer_lookup, search_term))
        elif action == 3:
            interactive_assign_golfers(participants, golfer_lookup)
        elif action == 4:
            if not participants:
                print("No participants to remove.")
                continue
            names = sorted(participants.keys())
            idx = prompt_choice("Remove which participant?", names, allow_back=True)
            if idx is None:
                continue
            participants.pop(names[idx], None)
        elif action == 5:
            return participants
        else:
            return None


def _ensure_config_shape(cfg, picked):
    cfg.setdefault("tournament_name", picked.get("name", ""))
    cfg.setdefault("source", {})
    if picked.get("pga_event_id"):
        cfg["source"]["pga_event_id"] = picked["pga_event_id"]
    cfg["source"].setdefault(
        "leaderboard_url",
        default_leaderboard_url_for_event_id(cfg["source"].get("pga_event_id")),
    )
    cfg.setdefault("pool_enabled", True)
    cfg.setdefault("participants", {})
    cfg.setdefault("output", {})
    cfg["output"].setdefault("google_sheets", {
        "enabled": True,
        "sheet_url": "",
        "worksheet_name": "Sheet1",
        "service_account_env": "GOOGLE_SERVICE_ACCOUNT_JSON",
    })
    return cfg


def edit_google_sheets_settings(cfg, slug_hint):
    g = cfg.setdefault("output", {}).setdefault("google_sheets", {})
    default_ws = slug_hint or g.get("worksheet_name") or "Sheet1"
    while True:
        enabled = g.get("enabled", True)
        url_disp = str(g.get("sheet_url") or DEFAULT_POOL_SHEET_URL)
        url_menu = url_disp if len(url_disp) <= 56 else f"{url_disp[:53]}..."
        action = prompt_choice(
            "Google Sheets publishing",
            [
                f"Toggle enable — currently {'ON' if enabled else 'OFF'}",
                f"Sheet URL — {url_menu}",
                f"Worksheet tab — {g.get('worksheet_name', 'Sheet1')} (unique tab per simultaneous tournament)",
                "← Back (tournament editor)",
            ],
            allow_back=False,
        )
        if action == 0:
            yn = prompt_yes_no("Publish to Google Sheets?", default=enabled, allow_back=True)
            if yn is None:
                continue
            g["enabled"] = yn
        elif action == 1:
            cur = g.get("sheet_url") or ""
            line = prompt_line(
                f"Sheet URL [Enter = default pool spreadsheet]",
                default=cur or DEFAULT_POOL_SHEET_URL,
                allow_back=True,
            )
            if line is None:
                continue
            g["sheet_url"] = line.strip() if line.strip() else DEFAULT_POOL_SHEET_URL
        elif action == 2:
            line = prompt_line(
                f"Worksheet tab name [default: {default_ws}]",
                default=g.get("worksheet_name") or default_ws,
                allow_back=True,
            )
            if line is None:
                continue
            g["worksheet_name"] = line.strip() or default_ws
        else:
            return


def edit_tournament_config_file(path, picked=None):
    path = Path(path)
    if picked is None:
        picked = {"kind": "local", "slug": path.stem, "name": path.stem, "pga_event_id": None}
    print(f"\nEditing config: {path}")

    # Snapshot of file as it existed before this edit session (for revert on exit without save).
    if path.exists():
        raw_on_disk = load_json_file(path)
        disk_snapshot = copy.deepcopy(raw_on_disk)
        cfg = copy.deepcopy(raw_on_disk)
    else:
        disk_snapshot = None
        cfg = {}
    cfg = _ensure_config_shape(cfg, picked)

    if disk_snapshot is None:
        yn = prompt_yes_no(
            "Confirm leaderboard page URL for this event's tour (S/H/Y pages differ; dual R* weeks may need secondary URL)?",
            default=True,
            allow_back=True,
        )
        if yn is True:
            edit_leaderboard_url_menu(cfg)

    slug_hint = picked.get("slug", path.stem)

    while True:
        lb_disp = _short_url(resolve_leaderboard_url(cfg.get("source", {})))
        action = prompt_choice(
            "Tournament editor — choose an option:",
            [
                f"Tournament display name — {cfg.get('tournament_name', '')}",
                f"Pool scoring — {'ON' if cfg.get('pool_enabled', True) else 'OFF'}",
                f"PGA event id — {cfg.get('source', {}).get('pga_event_id') or '(unset; uses first leaderboard on page)'}",
                f"Leaderboard page URL — {lb_disp}",
                "Participants & picks",
                "Google Sheets (URL & worksheet tab)",
                "Save and exit",
                "Exit without saving",
            ],
            allow_back=False,
        )
        if action == 0:
            cur = cfg.get("tournament_name", "")
            line = prompt_line(f"Tournament display name", default=cur, allow_back=True)
            if line is None:
                continue
            cfg["tournament_name"] = line.strip()
        elif action == 1:
            yn = prompt_yes_no("Enable pool scoring?", default=cfg.get("pool_enabled", True), allow_back=True)
            if yn is None:
                continue
            cfg["pool_enabled"] = yn
        elif action == 2:
            cur = cfg.get("source", {}).get("pga_event_id") or ""
            line = prompt_line(
                "PGA tournament / leaderboard id (e.g. R2026020 from site JSON)",
                default=cur,
                allow_back=True,
            )
            if line is None:
                continue
            line = line.strip()
            cfg.setdefault("source", {})
            if line:
                cfg["source"]["pga_event_id"] = line
            else:
                cfg["source"].pop("pga_event_id", None)
            new_default = default_leaderboard_url_for_event_id(cfg["source"].get("pga_event_id"))
            yn = prompt_yes_no(
                f"Set leaderboard URL to tour default?\n  {new_default}",
                default=True,
                allow_back=True,
            )
            if yn is True:
                cfg["source"]["leaderboard_url"] = new_default
        elif action == 3:
            edit_leaderboard_url_menu(cfg)
        elif action == 4:
            result = prompt_for_participants(cfg.get("participants", {}), cfg.get("source", {}))
            if result is None:
                continue
            cfg["participants"] = result
        elif action == 5:
            edit_google_sheets_settings(cfg, slug_hint)
        elif action == 6:
            save_json_file(path, cfg)
            print(f"Saved tournament config: {path}")
            return cfg
        else:
            if disk_snapshot is not None:
                save_json_file(path, copy.deepcopy(disk_snapshot))
                print("Restored file to how it was when you opened this editor; in-memory edits discarded.")
            else:
                print("Discarded edits; no tournament file was on disk when you started.")
            return None


def configure_tournament_from_menu():
    print("Fetching PGA Tour leaderboard page for schedule + configuration...")
    site_json = fetch_site_json(DEFAULT_LEADERBOARD_URL)
    picked = pick_tournament_entry(site_json, allow_back=True)
    if picked is None:
        return

    path = TOURNAMENTS_DIR / f"{picked['slug']}.json"

    if picked["kind"] == "local":
        cfg = load_json_file(path) if path.exists() else {}
        picked_meta = {
            "kind": "local",
            "slug": picked["slug"],
            "name": picked.get("name") or picked["slug"],
            "pga_event_id": picked.get("pga_event_id"),
        }
    else:
        cfg = load_json_file(path) if path.exists() else {}
        picked_meta = {
            "kind": "pga",
            "slug": picked["slug"],
            "name": picked["name"],
            "pga_event_id": picked.get("pga_event_id"),
        }

    result = edit_tournament_config_file(path, picked=picked_meta)
    if result is None:
        return
    yn = prompt_yes_no("Set as active tournament now?", default=True, allow_back=True)
    if yn is True:
        set_active_tournament(picked["slug"])
    return result


def admin_mode():
    while True:
        action = prompt_choice(
            "Admin mode actions:",
            [
                "Configure tournament (PGA active/upcoming or local config)",
                "Set active tournament (from saved configs only)",
                "Exit admin mode",
            ],
            allow_back=False,
        )
        if action == 0:
            configure_tournament_from_menu()
        elif action == 1:
            local = list_local_tournaments()
            if not local:
                print("No local tournaments found.")
                continue
            idx = prompt_choice(
                "Set active tournament to:",
                [f"{t['name']} ({t['slug']})" for t in local],
                allow_back=True,
            )
            if idx is None:
                continue
            set_active_tournament(local[idx]["slug"])
        else:
            break


def parse_relative_to_par(score):
    """Total vs par: E, blank, or '-' before scoring starts => 0."""
    if score is None:
        return 0
    s = str(score).strip()
    if s in ("", "E", "-", "—"):
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def get_worksheet(config):
    import gspread
    from google.oauth2.service_account import Credentials

    google_cfg = config["output"]["google_sheets"]
    if not google_cfg.get("enabled", False):
        return None

    creds_path = os.getenv(google_cfg.get("service_account_env", "GOOGLE_SERVICE_ACCOUNT_JSON"))
    if not creds_path:
        raise ValueError("Google Sheets output enabled but service account env var is not set")

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(creds_path, scopes=scope)
    client = gspread.authorize(creds)
    sheet_url = (google_cfg.get("sheet_url") or "").strip() or DEFAULT_POOL_SHEET_URL
    sheet = client.open_by_url(sheet_url)
    return sheet.worksheet(google_cfg.get("worksheet_name", "Sheet1"))


def compute_scores(df, participant_selections):
    participant_scores = {}
    missing_ids = {}

    for participant, selections in participant_selections.items():
        total_score = 0
        projected_winnings = 0
        net_score = 0
        for golfer in selections:
            golfer_data = df[df["PlayerID"].astype(str) == str(golfer)]
            if golfer_data.empty:
                missing_ids.setdefault(participant, []).append(golfer)
                continue
            points = golfer_data.iloc[0]["Points"]
            winnings = golfer_data.iloc[0]["Projected_Winnings"]
            score = golfer_data.iloc[0]["Score"]
            total_score += points
            projected_winnings += winnings
            net_score += parse_relative_to_par(score)
        participant_scores[participant] = (total_score, projected_winnings, net_score)

    sorted_scores = dict(sorted(participant_scores.items(), key=lambda item: item[1][2]))
    return sorted_scores, missing_ids


def publish_google_sheet(worksheet, title, timestamp, sorted_scores, participant_selections, df):
    worksheet.clear()
    dt_str = timestamp.strftime("%m/%d/%Y %H:%M")
    worksheet.update(range_name="A1", values=[[title]])
    worksheet.update(range_name="A2", values=[["Timestamp: ", dt_str]])
    worksheet.update(range_name="A4", values=[["Current Standings:"]])
    worksheet.update(range_name="A5", values=[["Participant", "Net over/under"]])

    values = [[participant, int(net_score)] for participant, (_, _, net_score) in sorted_scores.items()]
    if values:
        worksheet.append_rows(values)

    worksheet.update(range_name="A12", values=[["Participant Standings Detail:"]])
    for sorted_participant, (_, _, net_score) in sorted_scores.items():
        worksheet.append_rows([[sorted_participant, int(net_score)]])
        selections = participant_selections[sorted_participant]
        participant_df = pd.DataFrame()
        for golfer in selections:
            golfer_data = df[df["PlayerID"].astype(str) == str(golfer)]
            if participant_df.empty:
                participant_df = golfer_data.copy()
            else:
                participant_df = pd.concat([participant_df, golfer_data], ignore_index=True)

        selected_columns = participant_df[["Name", "Position", "Today", "Thru", "Score", "Rounds", "Total", "Points"]]
        selected_columns = selected_columns.sort_values(by="Points", ascending=False)
        selected_columns = selected_columns[["Name", "Score", "Position", "Today", "Thru", "Rounds", "Total"]]
        values = [selected_columns.columns.tolist()] + selected_columns.values.tolist()
        worksheet.append_rows(values)
        worksheet.append_rows([["_"]])


def main(config):
    print(f"[{datetime.now()}] Running main()")
    now = datetime.now()
    title = config["tournament_name"]
    participants = config["participants"]
    pool_enabled = config["pool_enabled"]

    print(f"{title} Pool Standings:")
    print("Timestamp:", now)
    print()

    src = config.get("source", {})
    lb_url = resolve_leaderboard_url(src)
    print(f"Leaderboard page: {lb_url}")
    print(f"PGA event id: {src.get('pga_event_id') or '(none)'}")
    print()

    df = get_data(
        now,
        config["backup"],
        pga_event_id=src.get("pga_event_id"),
        leaderboard_url=lb_url,
    )

    if not pool_enabled or not participants:
        print("Pool disabled or no participants configured; scrape completed.")
        return

    sorted_scores, missing_ids = compute_scores(df, participants)
    for participant, (score, projected_winnings, net_score) in sorted_scores.items():
        print(f"{participant}: {score} {projected_winnings} {net_score}")

    if missing_ids:
        print("\nWarning: Some selected golfer IDs were not found in the leaderboard:")
        for participant, golfer_ids in missing_ids.items():
            print(f"  {participant}: {', '.join(golfer_ids)}")

    print("\nParticipant Standings Detail:\n")
    for sorted_participant, (score, projected_winnings, net_score) in sorted_scores.items():
        print(f"{sorted_participant}: {score} {projected_winnings} {net_score}")
        selections = participants[sorted_participant]
        participant_df = pd.DataFrame()
        for golfer in selections:
            golfer_data = df[df["PlayerID"].astype(str) == str(golfer)]
            if participant_df.empty:
                participant_df = golfer_data.copy()
            else:
                participant_df = pd.concat([participant_df, golfer_data], ignore_index=True)
        if participant_df.empty:
            print("No golfer data found for this participant.\n")
            continue
        selected_columns = participant_df[["Name", "Position", "Today", "Thru", "Score", "Rounds", "Total", "Points"]]
        selected_columns = selected_columns.sort_values(by="Points", ascending=False)
        selected_columns = selected_columns[["Name", "Score", "Position", "Today", "Thru", "Rounds", "Total"]]
        print(selected_columns.to_string(justify="center", index=False))
        print()

    worksheet = get_worksheet(config)
    if worksheet is not None:
        publish_google_sheet(worksheet, title, now, sorted_scores, participants, df)


def get_data(now_dt, backup_config, pga_event_id=None, leaderboard_url=None, json_data=None):
    """Fetch leaderboard HTML from pgatour; select event via pga_event_id (queryKey) within __NEXT_DATA__."""
    fetch_url = (leaderboard_url or "").strip() or DEFAULT_LEADERBOARD_URL
    if json_data is None:
        response = requests.get(fetch_url, timeout=30)
        if response.status_code != 200:
            print("Failed to retrieve data:", response.status_code)
            return pd.DataFrame()
        soup = BeautifulSoup(response.content, "html.parser")
        script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
        if not script_tag:
            print("JSON data not found on the page.")
            return pd.DataFrame()
        json_data = json.loads(script_tag.string)

    if backup_config.get("enabled", False) and now_dt.minute == 0:
        save_json(json_data, backup_config["directory"])

    leaderboard_data = find_leaderboard_players(json_data, pga_event_id=pga_event_id)
    if leaderboard_data is None:
        print("Unable to find leaderboard players data in page JSON.")
        return pd.DataFrame()

    # extract desired data from selected JSON and create dataframe
    selected_columns = [{"PlayerID": row.get("player", {}).get("id"),
                         "Position": row.get("scoringData", {}).get("position"),
                         "Name": row.get("player", {}).get("displayName"),
                         "Rounds": row.get("scoringData", {}).get("rounds"),
                         "Score": row.get("scoringData", {}).get("total"),
                         "Today": row.get("scoringData", {}).get("score"),
                         "Thru": row.get("scoringData", {}).get("thru"),
                         "Time": row.get("scoringData", {}).get("teeTime"),
                         "backNine": row.get("scoringData", {}).get("backNine"),
                         "Total": row.get("scoringData", {}).get("totalStrokes")}
                        for row in leaderboard_data]
    df = pd.DataFrame(selected_columns)

    # convert rounds field to string so it can be stored in database
    df["Rounds"] = df["Rounds"].apply(lambda x: ",".join(map(str, x)) if x is not None else "")

    # Count number of participants in the tournament
    max_points = df["Name"].notna().sum()

    # Check if Thru is empty - if so, update Thru with starting time
    df.loc[df["Thru"] == "", "Thru"] = df[df["Thru"] == ""].apply(lambda row: teeTime(row["Time"], row["backNine"]), axis=1)

    # create new column PositionNum from Position in dataframe to be cleaned and casted to int
    # strip any leading 'T' values indicating ties in the position column
    df["PositionNum"] = df["Position"].str.replace("^T", "", regex=True)

    # convert the position column to numeric values
    df["PositionNum"] = pd.to_numeric(df["PositionNum"], errors="coerce")

    # convert NaN values for position to 0
    df["PositionNum"] = np.where(df["PositionNum"].notna(), df["PositionNum"], 0)

    # Create and calculate points column - if not a number assign 0
    df["Points"] = np.where(df["PositionNum"].notna(), max_points - df["PositionNum"] + 1, 0)

    # correct points for players with position 0 - ie DQ and MC
    df["Points"] = np.where(df["Points"] > max_points, 0, df["Points"])

    # cast Position and Points as integer from float
    df["PositionNum"] = df["PositionNum"].astype(int)
    df["Points"] = df["Points"].astype(int)

    # Assign rankings to calculate projected winnings
    df["Rank"] = assign_rank(df["PositionNum"])

    # Get Projected Winnings based on Rank
    df["Projected_Winnings"] = df["Rank"].map(get_prize_money)

    # save dataframe to CSV if needed - else comment out
    # df.to_csv('golfers.csv')
    # print df if needed - else comment out
    # print(df)

    # return dataframe
    return df


def assign_rank(position):
    # assign rank brackets by position, grouping ties, for assignment of payout
    rank = 0
    prev_position = None
    ranks = []
    for pos in position:
        if pos != prev_position:
            rank += 1
        if pos == 0:
            rank = 0
        ranks.append(rank)
        prev_position = pos
    return ranks


def get_prize_money(rank):
    # create dictionary containing prize money estimates using 2023 payouts by rank
    prize_money = {1: 3240000, 2: 1584000, 3: 744000, 4: 580500, 5: 522000, 6: 432000, 7: 333000,
                   8: 261000, 9: 187200, 10: 147000, 11: 125100, 12: 111600, 13: 97200, 14: 79200,
                   15: 66600, 16: 57600, 17: 50760, 18: 46080, 19: 44280, 20: 43200}

    # assign prize money by rank
    if rank > 20:
        return 40000
    return prize_money.get(rank, 0)


def save_json(data, backup_directory):

    # make sure directory exists
    if not os.path.exists(backup_directory):
        os.makedirs(backup_directory)

    # create unique filename
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"backup_{timestamp}.json"

    # write data to file
    with open(os.path.join(backup_directory, filename), "w", encoding="utf-8") as file:
        json.dump(data, file)


def teeTime(time_m, backNine):

    # convert time to seconds from milliseconds
    time_s = time_m / 1000

    # get UTC time
    time_utc = datetime.fromtimestamp(time_s, tz=pytz.UTC)

    # convert to EST
    est = pytz.timezone("US/Eastern")

    time_est = time_utc.astimezone(est)

    # format time as H:M and add an asterisk if player teeing off on back nine
    time_formatted = time_est.strftime("%l:%M %p")

    if backNine:
        time_formatted += "*"

    return time_formatted


def seconds_until_next_10min():
    now = datetime.now()
    seconds_past_hour = now.minute * 60 + now.second
    wait = (600 - (seconds_past_hour % 600)) % 600
    return wait if wait != 0 else 600  # ensure at least a 1-second sleep


def run_loop(start_hour, end_hour, config):
    while True:
        now = datetime.now()

        # Check if we're in the allowed window
        if start_hour <= now.hour < end_hour:
            wait_seconds = seconds_until_next_10min()
            print(f"[{now}] Inside window. Sleeping for {int(wait_seconds)} seconds to align with 10-minute mark.")
            time.sleep(wait_seconds)

            now = datetime.now()
            print(f"[{now}] Kicking off main()")
            try:
                main(config)
            except Exception:
                print(f"[{datetime.now()}] Error during execution:")
                print(traceback.format_exc())
                print()  # blank line
        else:
            # Outside the allowed window — sleep until next 10-minute mark or until start_hour
            print(f"[{now}] Outside of allowed window ({start_hour}:00–{end_hour}:00).")

            # Calculate time until next check (10 minutes or until start_hour)
            minutes_until_start = ((start_hour - now.hour) % 24) * 60 - now.minute
            sleep_minutes = max(10, minutes_until_start)
            print(f"Sleeping for {sleep_minutes} minutes.")
            time.sleep(sleep_minutes * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run tournament pool standings scraper/publisher.")
    parser.add_argument("--tournament", help="Tournament config slug (e.g. open_2025)")
    parser.add_argument("--once", action="store_true", help="Run once instead of loop")
    parser.add_argument("--validate", action="store_true", help="Validate config and exit")
    parser.add_argument("--admin", action="store_true", help="Run interactive administration mode")
    args = parser.parse_args()

    if args.admin:
        admin_mode()
        raise SystemExit(0)

    tournament_id = resolve_tournament_id(args.tournament)
    config = load_config(tournament_id)
    validate_config(config)

    print(f"Loaded tournament config: {tournament_id}")

    if args.validate:
        print("Config validation passed.")
    elif args.once:
        main(config)
    else:
        start_hour = config["run_window"]["start_hour"]
        end_hour = config["run_window"]["end_hour"]
        run_loop(start_hour, end_hour, config)
