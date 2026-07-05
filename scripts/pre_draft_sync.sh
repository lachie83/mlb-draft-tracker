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
ORDER_CSV="${DRAFT_ORDER_CSV:-../examples/draft_order_seed_${YEAR}.csv}"

echo "==> Initializing database"
python3 main.py "${DB_ARGS[@]}" init-db

echo "==> Seeding official draft order from $ORDER_CSV"
python3 main.py "${DB_ARGS[@]}" seed-draft-order --year "$YEAR" --csv "$ORDER_CSV"

echo "==> Syncing prospects"
PROSPECTS_CSV="${PROSPECTS_SEED_CSV:-../examples/prospects_top250_seed_${YEAR}.csv}"
if python3 main.py verify-baseballr >/dev/null 2>&1; then
  echo "    baseballr is available, using it as the prospect source"
  python3 main.py "${DB_ARGS[@]}" sync-prospects --year "$YEAR"
elif [ -f "$PROSPECTS_CSV" ]; then
  echo "    baseballr is unavailable, seeding the top-250 CSV snapshot ($PROSPECTS_CSV)"
  python3 main.py "${DB_ARGS[@]}" seed-prospects-csv --year "$YEAR" --csv "$PROSPECTS_CSV"
else
  echo "    baseballr is unavailable and no CSV snapshot exists for $YEAR, using the no-R live-scrape fallback"
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
