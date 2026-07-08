#!/usr/bin/env bash
# Pre-draft sync: bring the local board, draft order, and predictions to a
# known-good state ahead of draft day. Every step is idempotent, so this is
# safe to re-run as often as you like (e.g. from a daily cron entry).
#
# Env overrides:
#   DRAFT_YEAR  draft year to operate on (default: 2026)
#   DRAFT_DB    path to the sqlite db (default: the app's built-in default)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../python_app"

YEAR="${DRAFT_YEAR:-2026}"
DB_ARGS=()
if [ -n "${DRAFT_DB:-}" ]; then
  DB_ARGS=(--db "$DRAFT_DB")
fi

echo "==> Initializing database"
python3 main.py "${DB_ARGS[@]}" init-db

echo "==> Syncing official draft order from the MLB Stats API"
python3 main.py "${DB_ARGS[@]}" sync-draft-order-api --year "$YEAR"

echo "==> Syncing prospects"
# /draft/prospects/{year} was empty pre-draft every time this was checked
# through 2026-07-07, but MLB populated it as of 2026-07-08 - live rank plus
# real scouting blurb/report text, richer than the CSV/no-R fallback. Try it
# first; if MLB hasn't populated it for some other year, or the call fails
# for any reason, fall through to baseballr, then the CSV/no-R chain that
# used to be the only option (unchanged, still here as a safety net).
PROSPECTS_CSV="${PROSPECTS_SEED_CSV:-../examples/prospects_top250_seed_${YEAR}.csv}"
if python3 main.py "${DB_ARGS[@]}" sync-prospects-api --year "$YEAR"; then
  echo "    synced prospects live from the MLB Stats API"
elif python3 main.py verify-baseballr >/dev/null 2>&1 && python3 main.py "${DB_ARGS[@]}" sync-prospects --year "$YEAR"; then
  echo "    MLB Stats API prospects unavailable, synced via baseballr instead"
elif [ -f "$PROSPECTS_CSV" ]; then
  echo "    MLB Stats API and baseballr both unavailable, seeding the top-250 CSV snapshot ($PROSPECTS_CSV)"
  python3 main.py "${DB_ARGS[@]}" seed-prospects-csv --year "$YEAR" --csv "$PROSPECTS_CSV"
else
  echo "    MLB Stats API and baseballr both unavailable, and no CSV snapshot exists for $YEAR, using the no-R live-scrape fallback"
  python3 main.py "${DB_ARGS[@]}" seed-no-r-prospects --year "$YEAR"
fi

echo "==> Generating heuristic predictions"
python3 main.py "${DB_ARGS[@]}" generate-predictions --year "$YEAR" --top-n 5 --max-pick 50

if [ "$YEAR" = "2026" ]; then
  echo "==> Seeding real mock draft picks and consensus predictions"
  python3 main.py "${DB_ARGS[@]}" seed-mock-drafts --year "$YEAR"
  python3 main.py "${DB_ARGS[@]}" seed-mock-consensus --year "$YEAR"
else
  echo "==> Skipping mock-consensus: no real mock draft data exists for $YEAR (only 2026)"
fi

echo "==> Pre-draft sync complete for $YEAR."
