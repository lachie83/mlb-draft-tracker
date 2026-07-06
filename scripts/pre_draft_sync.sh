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
# The MLB Stats API's per-year prospects list isn't populated pre-draft (verified
# empty for an upcoming draft), so the ranked pre-draft board still comes from
# baseballr when available, or the CSV/no-R fallback otherwise. Draft order and
# live picks (see live-monitor-api) don't have this gap.
PROSPECTS_CSV="${PROSPECTS_SEED_CSV:-../examples/prospects_top250_seed_${YEAR}.csv}"
if python3 main.py verify-baseballr >/dev/null 2>&1 && python3 main.py "${DB_ARGS[@]}" sync-prospects --year "$YEAR"; then
  echo "    synced prospects via baseballr"
elif [ -f "$PROSPECTS_CSV" ]; then
  echo "    baseballr unavailable or its live call failed, seeding the top-250 CSV snapshot ($PROSPECTS_CSV)"
  python3 main.py "${DB_ARGS[@]}" seed-prospects-csv --year "$YEAR" --csv "$PROSPECTS_CSV"
else
  echo "    baseballr unavailable or its live call failed, and no CSV snapshot exists for $YEAR, using the no-R live-scrape fallback"
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
