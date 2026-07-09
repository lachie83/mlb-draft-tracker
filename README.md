# MLB Draft 2026 Tracker

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/lachie83/mlb-draft-tracker/badge)](https://scorecard.dev/viewer/?uri=github.com/lachie83/mlb-draft-tracker)

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
### Draft order and live picks: MLB Stats API (preferred, no R needed)
`statsapi.mlb.com` is the official, free, unauthenticated JSON API that
powers mlb.com/draft/tracker — the same source baseballr wraps, but callable
directly with no Rscript dependency and no live-call fragility:
- `sync-draft-order-api` — fetches the *entire* draft scaffold (every round,
  every pick, real team + slot value), not a placeholder CSV. Verified
  against the live 2026 draft: 613 picks across 26 rounds including
  competitive-balance/supplemental rounds.
- `live-monitor-api` — fetches the full draft each call and reconciles any
  newly-drafted picks into `prospects`/`actual_picks`, firing Telegram
  alerts for new ones. Idempotent — safe to run on a schedule (see
  `scripts/poll_draft_day.sh`, which uses this by default).
- `on-the-clock-api` — prints who's currently on the clock from the
  lightweight `/draft/{year}/latest` endpoint.

One gap: the API's per-year prospects list (`/draft/prospects/{year}`) isn't
populated pre-draft (verified empty for the upcoming 2026 draft), so it
doesn't yet give a ranked pre-draft board on its own — see below for that.

### Prospect board: baseballr (R) or no-R fallback
For the ranked pre-draft prospect board specifically:
- **baseballr** (`sync-prospects`) — best source when R is available, via
  `baseballr::mlb_draft_prospects()`. Requires `Rscript` plus R packages:
  `baseballr`, `DBI`, `RSQLite`, `jsonlite`. This is the one remaining
  place R is used; see the R / baseballr mode section below.
- **No-R fallback** — works when `Rscript` is unavailable, or as a more
  reliable default (see `pre_draft_sync.sh`, which now falls back
  automatically if baseballr's live call fails even when R itself is
  installed correctly). Two options, and they can be combined (later
  upserts win on conflict):
  - `seed-no-r-prospects` — live scrape of MLB Pipeline's draft page plus a
    curated top-40 fallback list; small but self-refreshing each run.
  - `seed-prospects-csv` — loads `examples/prospects_top250_seed_2026.csv`,
    a full top-250 draft board snapshot (rank, name, position, school, age,
    height/weight, bats/throws). It's a point-in-time export (captured by
    manually browsing MLB Pipeline's draft rankings page), not a live
    source, so refresh the CSV periodically as rankings move. `person_id`
    values in this CSV are synthetic (not real MLB person ids), since the
    rankings page doesn't expose them.

## Quick start
```bash
cd python_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python3 main.py init-db
python3 main.py sync-draft-order-api --year 2026
python3 main.py seed-prospects-csv --year 2026
python3 main.py generate-predictions --year 2026 --top-n 5 --max-pick 20
python3 main.py seed-mock-drafts --year 2026
python3 main.py seed-mock-consensus --year 2026
python3 dashboard.py --host 0.0.0.0 --port 8000
```

During the live draft:
```bash
python3 main.py live-monitor-api --year 2026   # one-shot: fetch + reconcile + alert
python3 main.py on-the-clock-api --year 2026   # who's up right now
```

## Predictions
Two independent prediction models are generated per pick and shown side by
side in the dashboard's Model Comparison view:
- **`heuristic_v1`** — `generate-predictions`. A rank/mock-shape/team-fit
  heuristic with no external mock-draft input.
- **`mock_consensus_v2`** — `seed-mock-drafts` + `seed-mock-consensus`.
  Aggregates real, dated, attributed mock draft picks (currently two MLB
  Pipeline mock drafts — see `python_app/mlb_tracker/real_mock_drafts_2026.py`
  for exact sources/dates/URLs) stored in the `mock_draft_picks` table. When
  multiple mocks name the same player for a pick, their weights are summed
  before normalizing into a probability, so agreement across sources
  produces a stronger consensus signal than any single mock. Every resulting
  prediction's `prediction_source` column lists exactly which mock(s) it
  came from.

Like the prospect CSV, `real_mock_drafts_2026.py` is a point-in-time
transcription of specific published mock drafts, not a live feed — it won't
include mock drafts published after it was written. Extend it (or add a
sibling module for other years) as new mocks come out.

```bash
python3 main.py seed-mock-drafts --year 2026     # populate mock_draft_picks
python3 main.py seed-mock-consensus --year 2026  # aggregate into predictions
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
Common workflows (pre-draft sync, a full draft-day rehearsal against real
past data, the draft-day polling loop, a Telegram test message, poller
status/recovery) are wrapped in `Makefile` targets and `scripts/*.sh` so
you don't need to remember flag combinations:
```bash
make pre-draft-sync
make test-telegram
make rehearse-draft-day
make poll-draft-day
make live-monitor-status
make help   # full list of targets
```
**Not sure what to actually run and when?** See the "TL;DR" at the top of
`docs/OPERATIONS.md` — it boils the whole draft day down to three commands.
That doc also has the full runbook, a Telegram setup walkthrough, cron
examples, and safe restart / recovery steps.

## Docker
Looking to run this in production on Kubernetes (AKS) instead? See
`docs/AKS_DEPLOYMENT.md` — cost-minimized cluster, durable storage, HTTPS
via cert-manager/Let's Encrypt, Telegram secrets in a K8s Secret, and a
fully automated draft-day operational loop. It uses `Dockerfile.k8s` (leaner,
no R) rather than the `Dockerfile` below.

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
Only used for the pre-draft prospect board now (`sync-prospects`) — draft
order and live picks use the MLB Stats API directly and need no R at all.

Install R, then install required R packages:
```bash
Rscript -e "install.packages(c('baseballr','DBI','RSQLite','jsonlite'))"
```

Verify your setup:
```bash
python3 main.py verify-baseballr
```

When verification passes:
```bash
python3 main.py sync-prospects --year 2026
```

`verify-baseballr` only checks that R/packages are installed, not that the
live call actually succeeds right now — `pre_draft_sync.sh` already handles
that by falling back automatically. If you're calling `sync-prospects`
directly and it fails, fall back manually:
```bash
python3 main.py seed-prospects-csv --year 2026
# or, for a live (smaller) scrape instead of the CSV snapshot:
python3 main.py seed-no-r-prospects --year 2026
```

## When to use each mode
- **Draft order and live picks**: always use the MLB Stats API
  (`sync-draft-order-api`, `live-monitor-api`) — it's the primary path now,
  not a fallback.
- **Prospect board**: prefer **baseballr** for the richest pre-draft
  ranking data when R is set up and working; use the **no-R fallback**
  (`seed-prospects-csv` / `seed-no-r-prospects`) otherwise, for local
  demos, testing, bootstrap seeding, or whenever baseballr's live call is
  failing even with R installed correctly.

## Environment variables
Use a local `.env` file or exported shell variables for secrets such as Telegram bot credentials.

See `.env.example`.

## Notes
- The legacy CSV-based `seed-draft-order` (`examples/draft_order_seed_*.csv`) still exists but is superseded by `sync-draft-order-api`, which pulls the real, complete order (including compensation/competitive-balance rounds) directly from the MLB Stats API instead of a hand-maintained placeholder file.
- `examples/prospects_top250_seed_2026.csv` is a manually-captured snapshot of MLB Pipeline's draft rankings (see `seed-prospects-csv` above); it will drift from the live board over time and should be refreshed periodically, and its `person_id`s are synthetic rather than real MLB ids.
- The MLB Stats API's `/draft/prospects/{year}` endpoint isn't populated pre-draft (verified empty for the upcoming 2026 draft), so it can't yet replace the prospect-board sources above — only draft order and live picks are fully covered by the API today.
- The SQLite DB is intentionally excluded from git; recreate it from schema + seed data.

## Roadmap
- automate periodic refresh of the top-250 prospect CSV snapshot, or drop it once/if `/draft/prospects/{year}` becomes populated pre-draft
- fully retire the R/baseballr path once the Stats API (or another source) covers the pre-draft prospect board too
- improve dashboard filtering and draft-day views
- add mock drafts from additional outlets beyond MLB Pipeline, and automate periodic re-transcription as new mocks are published
