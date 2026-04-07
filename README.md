# Golf Pool Tournament Tracker

`tourney.py` scrapes PGA Tour leaderboard data, calculates participant pool standings, and optionally publishes standings to Google Sheets.

The script supports multiple tours and tournament-specific configs:

- `R*` -> PGA Tour
- `S*` -> PGA Tour Champions
- `H*` -> Korn Ferry Tour
- `Y*` -> PGA Tour Americas

## What This Project Does

- Scrapes leaderboard JSON from PGA Tour pages (`__NEXT_DATA__`)
- Selects the correct tournament field using `pga_event_id`
- Calculates participant scores from configured golfer picks
- Prints standings + detailed participant breakdown to terminal
- Optionally writes standings to Google Sheets
- Provides an interactive admin mode to manage tournaments and picks

## Files

- `tourney.py` - main script
- `requirements.txt` - Python dependencies
- `config/global.json` - global defaults and active tournament
- `config/tournaments/*.json` - tournament-specific configs
- `config/tournaments/_template.json` - template for new tournament config

## Setup

## 1) Create and activate a virtualenv

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 2) Install dependencies

```bash
pip install -r requirements.txt
```

## 3) Configure Google service account (optional, required only if Sheets publishing is enabled)

Set the environment variable used by config (`GOOGLE_SERVICE_ACCOUNT_JSON` by default):

```bash
export GOOGLE_SERVICE_ACCOUNT_JSON="/absolute/path/to/service-account.json"
```

If this is not set and Google Sheets output is enabled, you will see:

`ValueError: Google Sheets output enabled but service account env var is not set`

## Running the Script

### Validate config only

```bash
python tourney.py --validate
```

### Run once (good for testing)

```bash
python tourney.py --once
```

### Run continuous loop

```bash
python tourney.py
```

Loop mode runs on 10-minute boundaries during `run_window.start_hour` to `run_window.end_hour` in your config.

### Run specific tournament slug

```bash
python tourney.py --tournament h2026129 --once
```

If `--tournament` is omitted, resolution order is:

1. CLI flag `--tournament`
2. `TOURNAMENT` environment variable
3. `config/global.json` -> `active_tournament`
4. default constant in code

## Admin Mode (Interactive)

```bash
python tourney.py --admin
```

Admin mode lets you:

- Configure tournaments from active/upcoming PGA schedule + local configs
- Set `active_tournament`
- Edit tournament metadata, participants, leaderboard URL, and Google Sheets settings
- Search/list golfers and interactively assign picks

## Configuration

## Global config: `config/global.json`

Typical keys:

- `run_window`: scraping window for loop mode
- `backup`: optional JSON backup settings
- `output.google_sheets`: default publishing settings
- `active_tournament`: slug currently used by default

## Tournament config: `config/tournaments/<slug>.json`

Important keys:

- `tournament_name`
- `pool_enabled`
- `participants`: map of participant name -> golfer ID array
- `source.pga_event_id`: tournament ID such as `H2026129`
- `source.leaderboard_url`: optional override URL (admin can set/confirm)
- `output.google_sheets`: tournament-level override of sheet settings

Use `_template.json` as a starting point.

## Tour URL Defaults

These are used to suggest/derive leaderboard URLs:

- `R` -> `https://www.pgatour.com/leaderboard`
- `S` -> `https://www.pgatour.com/pgatour-champions/leaderboard`
- `H` -> `https://www.pgatour.com/korn-ferry-tour/leaderboard`
- `Y` -> `https://www.pgatour.com/americas/leaderboard`

For weeks with two active `R*` events (major + opposite-field), admin mode supports selecting a secondary PGA Tour URL or entering a custom URL.

## Google Sheets Notes

- If no sheet URL is set, code defaults to the configured pool sheet URL constant.
- For simultaneous tournaments, use separate worksheet tab names (for example `h2026129`, `r2026041`) under the same spreadsheet.

## Common Troubleshooting

- **No matching leaderboard query for event ID**
  - Verify `source.pga_event_id` is correct for the selected event.
  - Confirm the selected leaderboard page URL is the right tour page.
  - Test with `python tourney.py --once` and check printed:
    - `Leaderboard page: ...`
    - `PGA event id: ...`

- **Scores like `-` cause parsing issues**
  - Current logic treats `E`, `-`, blank, and unknown score tokens as `0` for net pool scoring.

- **Google auth error**
  - Ensure `GOOGLE_SERVICE_ACCOUNT_JSON` is set and points to a valid service account file.

## Suggested `.gitignore` Entries

At minimum:

```gitignore
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.env
.env.*
*.log
```

Optional (if you want to avoid committing local/private data):

```gitignore
data.json
config/tournaments/*.json
!config/tournaments/_template.json
```
