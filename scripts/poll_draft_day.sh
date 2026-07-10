#!/usr/bin/env bash
# Draft-day live polling loop: repeatedly runs `main.py live-monitor-api` (the
# MLB Stats API path - no Rscript/baseballr dependency) on an interval and
# logs each run. Refuses to start a second overlapping poller against the
# same draft year, and stops itself after repeated failures instead of
# spamming retries forever.
#
# Env overrides:
#   DRAFT_YEAR           draft year to monitor (default: 2026)
#   DRAFT_DB             path to the sqlite db (default: the app's built-in default)
#   POLL_INTERVAL_SECONDS  seconds between polls. If unset (the default), the
#                         interval is recomputed every tick from
#                         `main.py draft-poll-interval` - for the 2026 draft
#                         that follows the real broadcast schedule in
#                         mlb_tracker/draft_schedule.py (as fast as every 10s
#                         during the densest window, 60s outside draft hours).
#                         Set this explicitly to pin a fixed interval instead
#                         (e.g. for a rehearsal) and skip the per-tick lookup.
#   POLL_PIDFILE          lock file path (default: /tmp/mlb-draft-tracker-live-monitor-<year>.pid)
#   POLL_LOGFILE          log file path (default: ../data/live-monitor-<year>.log)
#   LIVE_MONITOR_CMD      main.py subcommand to run each tick (default: live-monitor-api;
#                         set to "live-monitor" to use the legacy baseballr path instead)
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../python_app"

YEAR="${DRAFT_YEAR:-2026}"
DB_ARGS=()
if [ -n "${DRAFT_DB:-}" ]; then
  DB_ARGS=(--db "$DRAFT_DB")
fi
FIXED_INTERVAL="${POLL_INTERVAL_SECONDS:-}"
PIDFILE="${POLL_PIDFILE:-/tmp/mlb-draft-tracker-live-monitor-${YEAR}.pid}"
LOGFILE="${POLL_LOGFILE:-../data/live-monitor-${YEAR}.log}"
LIVE_MONITOR_CMD="${LIVE_MONITOR_CMD:-live-monitor-api}"
MAX_CONSECUTIVE_FAILURES=5
FALLBACK_INTERVAL=60

log() {
  echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOGFILE"
}

current_interval() {
  if [ -n "$FIXED_INTERVAL" ]; then
    echo "$FIXED_INTERVAL"
    return
  fi
  local dynamic
  dynamic=$(python3 main.py "${DB_ARGS[@]}" draft-poll-interval --year "$YEAR" 2>>"$LOGFILE")
  if [ -n "$dynamic" ] && [ "$dynamic" -gt 0 ] 2>/dev/null; then
    echo "$dynamic"
  else
    log "draft-poll-interval lookup failed, falling back to ${FALLBACK_INTERVAL}s"
    echo "$FALLBACK_INTERVAL"
  fi
}

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null; then
  echo "A live-monitor loop is already running for $YEAR (pid $(cat "$PIDFILE"))." >&2
  echo "Refusing to start a second one against the same draft year." >&2
  echo "If that process is actually gone, remove $PIDFILE and re-run." >&2
  exit 1
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"; log "stopped"; exit 0' INT TERM

if [ -n "$FIXED_INTERVAL" ]; then
  log "starting live-monitor loop for $YEAR (fixed interval ${FIXED_INTERVAL}s, pidfile $PIDFILE, command: $LIVE_MONITOR_CMD)"
else
  log "starting live-monitor loop for $YEAR (dynamic interval per draft_schedule.py, pidfile $PIDFILE, command: $LIVE_MONITOR_CMD)"
fi

failures=0
while true; do
  if python3 main.py "${DB_ARGS[@]}" "$LIVE_MONITOR_CMD" --year "$YEAR" >>"$LOGFILE" 2>&1; then
    failures=0
  else
    failures=$((failures + 1))
    log "live-monitor run failed (consecutive failure #$failures) - see $LOGFILE for details"
    if [ "$failures" -ge "$MAX_CONSECUTIVE_FAILURES" ]; then
      log "$MAX_CONSECUTIVE_FAILURES consecutive failures, stopping loop for manual investigation"
      rm -f "$PIDFILE"
      exit 1
    fi
  fi
  interval=$(current_interval)
  # Run sleep in the background and wait on it (rather than a plain foreground
  # `sleep`) so the INT/TERM trap fires immediately instead of waiting for the
  # full interval to elapse.
  sleep "$interval" &
  wait $!
done
