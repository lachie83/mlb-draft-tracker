#!/usr/bin/env bash
# Quick recovery/status check for the draft-day poller: whether a poller is
# currently running, the tail of its log, and the most recent picks recorded
# in the database. Run this before restarting `poll_draft_day.sh` after a
# crash so you know what state you're recovering into.
#
# Env overrides: DRAFT_YEAR, DRAFT_DB, POLL_PIDFILE, POLL_LOGFILE (same
# defaults as poll_draft_day.sh).
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../python_app"

YEAR="${DRAFT_YEAR:-2026}"
DB_ARGS=()
if [ -n "${DRAFT_DB:-}" ]; then
  DB_ARGS=(--db "$DRAFT_DB")
fi
PIDFILE="${POLL_PIDFILE:-/tmp/mlb-draft-tracker-live-monitor-${YEAR}.pid}"
LOGFILE="${POLL_LOGFILE:-../data/live-monitor-${YEAR}.log}"

echo "== Poller status ($YEAR) =="
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "RUNNING (pid $(cat "$PIDFILE"))"
else
  echo "NOT RUNNING"
  if [ -f "$PIDFILE" ]; then
    echo "(stale pidfile found at $PIDFILE - safe to remove before restarting)"
  fi
fi

echo
echo "== Last 20 log lines ($LOGFILE) =="
if [ -f "$LOGFILE" ]; then
  tail -n 20 "$LOGFILE"
else
  echo "(no log file yet)"
fi

echo
echo "== Last 5 actual picks recorded =="
python3 - "$YEAR" "${DB_ARGS[@]:-}" <<'PYEOF'
import sqlite3
import sys

from mlb_tracker.db import DEFAULT_DB_PATH

year = int(sys.argv[1])
db_args = sys.argv[2:]
db_path = db_args[db_args.index("--db") + 1] if "--db" in db_args else DEFAULT_DB_PATH

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT pick_number, team_name, player_name, updated_at FROM actual_picks "
    "WHERE draft_year = ? ORDER BY pick_number DESC LIMIT 5",
    (year,),
).fetchall()
if not rows:
    print("(no picks recorded yet)")
for row in rows:
    print(f"#{row['pick_number']:>3}  {row['team_name']:<28} {row['player_name']:<28} updated {row['updated_at']}")
conn.close()
PYEOF

echo
echo "== Recovery notes =="
echo "- Picks are keyed by (draft_year, pick_number); restarting the poller"
echo "  re-reconciles from the live source and will not duplicate rows."
echo "- Telegram alerts are de-duplicated via telegram_events_sent, keyed by"
echo "  pick number and message hash, so a restart will not re-send picks"
echo "  that were already successfully alerted."
echo "- If the pidfile is stale (process confirmed gone), remove it before"
echo "  restarting: rm -f $PIDFILE"
