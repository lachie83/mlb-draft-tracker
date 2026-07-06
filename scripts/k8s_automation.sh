#!/usr/bin/env bash
# Kubernetes automation sidecar entrypoint (see docs/AKS_DEPLOYMENT.md).
#
# Runs pre_draft_sync.sh once immediately, starts poll_draft_day.sh
# continuously in the background, and re-runs pre_draft_sync.sh on an
# interval - this is the automated equivalent of manually following
# docs/OPERATIONS.md's "3. Suggested schedules" so nobody needs to
# remote-trigger anything by hand on draft day.
#
# If the background poller dies (e.g. after its own 5-consecutive-failures
# backoff), this script exits non-zero so Kubernetes restarts the container
# per its restartPolicy, rather than silently running with a dead poller.
#
# Env overrides:
#   DRAFT_YEAR                        draft year to track (default: 2026)
#   DRAFT_DB                          sqlite db path (default: /app/data/mlb_draft_2026.db)
#   PRE_DRAFT_SYNC_INTERVAL_SECONDS   seconds between periodic re-syncs (default: 21600 = 6h)
#   POLL_INTERVAL_SECONDS             seconds between live-monitor-api polls (default: 60,
#                                     passed straight through to poll_draft_day.sh)
set -uo pipefail

export DRAFT_YEAR="${DRAFT_YEAR:-2026}"
export DRAFT_DB="${DRAFT_DB:-/app/data/mlb_draft_2026.db}"
SYNC_INTERVAL="${PRE_DRAFT_SYNC_INTERVAL_SECONDS:-21600}"

log() {
  echo "[$(date -u +%FT%TZ)] automation: $*"
}

terminate=0
trap 'terminate=1' INT TERM

log "initial pre-draft-sync (year=$DRAFT_YEAR, db=$DRAFT_DB)"
/app/scripts/pre_draft_sync.sh

log "starting live-monitor polling loop in the background"
/app/scripts/poll_draft_day.sh &
POLL_PID=$!

while [ "$terminate" -eq 0 ]; do
  SECONDS=0
  while [ "$SECONDS" -lt "$SYNC_INTERVAL" ] && [ "$terminate" -eq 0 ]; do
    if ! kill -0 "$POLL_PID" 2>/dev/null; then
      log "live-monitor poller exited unexpectedly - exiting so Kubernetes restarts this container"
      exit 1
    fi
    sleep 5
  done
  [ "$terminate" -eq 1 ] && break
  log "periodic pre-draft-sync refresh"
  /app/scripts/pre_draft_sync.sh || log "periodic pre-draft-sync failed, will retry next interval"
done

log "stopping"
kill -TERM "$POLL_PID" 2>/dev/null || true
wait "$POLL_PID" 2>/dev/null || true
exit 0
