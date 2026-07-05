# MLB Draft 2026 Tracker

A local SQLite-backed tracker for the 2026 MLB Draft that stores prospects, draft order, live picks, prediction outputs, and Telegram delivery state.

## What this project does
- stores a local draft prospect board in SQLite
- stores draft slot / draft order data
- tracks actual picks as they happen
- generates prediction rows for likely team/prospect matches
- supports Telegram notifications for newly detected picks
- provides a lightweight local dashboard

## Project structure
- `python_app/` — Python CLI app, dashboard, ingest, predictions
- `r_prototype/` — R + `baseballr` prototype scripts
- `sql/` — SQLite schema
- `examples/` — seed data for order/prospects
- `docs/` — architecture, operations, preview docs

## Modes
### Preferred mode
Uses `baseballr::mlb_draft_prospects()` through R:
- best source for prospect + draft mapping when R is available
- supports live draft reconciliation logic
- requires `Rscript` plus R packages: `baseballr`, `DBI`, `RSQLite`, `jsonlite`

### No-R fallback mode
Uses direct MLB Pipeline page scraping and curated seed data:
- works when `Rscript` is unavailable
- useful for dashboard/demo/testing/bootstrap

## Quick start
```bash
cd python_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python3 main.py init-db
python3 main.py seed-draft-order --year 2026 --csv ../examples/draft_order_seed_2026.csv
python3 main.py seed-no-r-prospects --year 2026
python3 main.py generate-predictions --year 2026 --top-n 5 --max-pick 20
python3 main.py seed-mock-consensus --year 2026
python3 dashboard.py --host 0.0.0.0 --port 8000
```

## Testing
Core tracker logic (schema init, prospect/draft-slot upserts, prediction generation,
Telegram dedupe, and fallback ingest normalization) has an automated test suite using
`pytest`. Tests run fully offline — no database file, R, or network access required.

```bash
cd python_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

pytest
```

## Automation helpers
Common workflows (pre-draft sync, the draft-day polling loop, a Telegram
test message, poller status/recovery) are wrapped in `Makefile` targets and
`scripts/*.sh` so you don't need to remember flag combinations:
```bash
make pre-draft-sync
make test-telegram
make poll-draft-day
make live-monitor-status
make help   # full list of targets
```
See `docs/OPERATIONS.md` for the full draft-day runbook, cron examples, and
safe restart / recovery steps.

## Docker
### Build
```bash
docker build -t mlb-draft-tracker .
```

### Run dashboard
```bash
docker run --rm -it \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  mlb-draft-tracker
```

Open:
- `http://localhost:8000`

### Docker Compose
```bash
docker compose up --build
```

### Run CLI commands in Docker
```bash
docker run --rm -it \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  mlb-draft-tracker \
  python3 main.py init-db
```

```bash
docker run --rm -it \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  mlb-draft-tracker \
  python3 main.py sync-prospects --year 2026
```

```bash
docker run --rm -it \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  mlb-draft-tracker \
  python3 main.py generate-predictions --year 2026 --top-n 5 --max-pick 20
```

## R / baseballr mode
Install R, then install required R packages:
```bash
Rscript -e "install.packages(c('baseballr','DBI','RSQLite','jsonlite'))"
```

Verify your setup before running sync/monitor commands:
```bash
python3 main.py verify-baseballr
```

When verification passes:
```bash
python3 main.py sync-prospects --year 2026
python3 main.py live-monitor --year 2026
```

If `verify-baseballr` fails, fix the reported R / package issue or use no-R mode:
```bash
python3 main.py seed-no-r-prospects --year 2026
```

## When to use each mode
- Prefer **R + baseballr mode** for production sync/live monitoring and best prospect-to-pick mapping.
- Use **no-R fallback mode** when R is not available, for local demos, testing, or bootstrap seeding.

## Environment variables
Use a local `.env` file or exported shell variables for secrets such as Telegram bot credentials.

See `.env.example`.

## Notes
- The draft order seed is partially verified; compensation and special-round rows should be finalized before draft day.
- The no-R fallback currently seeds a partial board rather than a complete top 250.
- The SQLite DB is intentionally excluded from git; recreate it from schema + seed data.

## Roadmap
- expand no-R prospect ingest toward a fuller top-250 board
- parse official MLB draft order more completely
- add a second live pick source beyond `baseballr`
- improve dashboard filtering and draft-day views
- strengthen prediction model with more mock-draft inputs
