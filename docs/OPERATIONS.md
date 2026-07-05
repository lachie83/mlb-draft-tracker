# MLB Draft 2026 Tracker — Operations Plan

## 1. Objective
Run a local SQLite-backed tracker that:
- stores the 2026 draft prospect board
- stores the official draft order / pick slots
- updates actual picks during the draft
- generates Telegram notifications for each new pick
- maintains heuristic predictions for likely player/team outcomes

## 2. Components
- `python_app/main.py` — main CLI
- `sql/schema.sql` — SQLite schema
- `r_prototype/fetch_prospects.R` — R/baseballr sync prototype
- `r_prototype/live_monitor.R` — R/baseballr live pick sync prototype
- Telegram delivery via bot token + chat id env vars
- `Makefile` + `scripts/` — automation helpers for the workflows below (see §5a)

## 3. Suggested schedules
### Pre-draft (now through draft week)
- `sync-prospects`: daily at 7:00 UTC
- `seed-draft-order`: on demand whenever MLB updates the official order
- `generate-predictions`: daily at 8:00 UTC

### Draft week
- `sync-prospects`: every 6 hours
- `generate-predictions`: every 4 hours
- manual verification of seed CSV against official MLB order page

### Draft live window
Recommended poll cadence:
- 60 sec polling from 30 min before draft start through first round
- 90 sec polling for later rounds if alert fatigue is a concern

Live jobs:
- `live-monitor`: every 60 sec
- optional `generate-predictions`: every 15 min if ingesting fresh mock intel

## 4. Draft-day runbook
1. `make pre-draft-sync` — initialize database, seed official order, sync prospects, generate predictions
2. `make test-telegram` — confirm Telegram delivery with a dry-run message
3. `make poll-draft-day` — start the live-monitor polling loop
4. Verify first 3 picks manually against MLB.com
5. `make live-monitor-status` — spot-check the poller, log, and recorded picks at any point
6. Watch for duplicates / missing picks in `telegram_events_sent`

## 5. Example commands
```bash
cd python_app
python3 main.py init-db
python3 main.py sync-prospects --year 2026
python3 main.py seed-draft-order --year 2026 --csv ../examples/draft_order_seed_2026.csv
python3 main.py generate-predictions --year 2026 --top-n 5 --max-pick 50
python3 main.py live-monitor --year 2026
```

## 5a. Scripts and make targets
The commands above are wrapped by `Makefile` targets and `scripts/*.sh` so
draft-day operation doesn't require remembering flag combinations. All
targets accept `YEAR=<year>` (default `2026`) and `DB=<path>` (default: the
app's built-in db path) overrides, e.g. `make pre-draft-sync YEAR=2025`.

| Command | What it does |
| --- | --- |
| `make pre-draft-sync` | `scripts/pre_draft_sync.sh` — init db, seed draft order, sync prospects (baseballr if available, else no-R fallback), generate predictions, seed mock consensus. Idempotent — safe to re-run on a schedule. |
| `make poll-draft-day` | `scripts/poll_draft_day.sh` — runs `live-monitor` on a loop (default every 60s, override with `POLL_INTERVAL_SECONDS`). Refuses to start a second poller for the same year, and stops itself after 5 consecutive failures instead of looping forever. |
| `make live-monitor-status` | `scripts/live_monitor_status.sh` — shows whether a poller is running, the tail of its log, and the most recently recorded picks. Use this before restarting after a crash. |
| `make test-telegram` | Sends a one-off Telegram message via `main.py test-telegram` to confirm delivery. See §7. |
| `make init-db` / `sync-prospects` / `seed-no-r-prospects` / `seed-draft-order` / `generate-predictions` / `seed-mock-consensus` / `verify-baseballr` / `live-monitor` | Thin wrappers around the matching `main.py` subcommand. |
| `make dashboard` | Runs the local dashboard on `:8000`. |

Run `make help` for the full list.

## 6. Suggested cron entries
```cron
# daily pre-draft sync (idempotent)
0 7 * * * cd /path/to/mlb_draft_tracker && DRAFT_YEAR=2026 ./scripts/pre_draft_sync.sh >> data/pre_draft_sync.log 2>&1

# draft day poller (example only; adjust to actual draft schedule). The
# script itself loops internally, so this cron entry only needs to start
# it once — combine with a @reboot entry or a process supervisor if you
# want it to survive machine restarts too.
0 17 11 7 * cd /path/to/mlb_draft_tracker && DRAFT_YEAR=2026 POLL_INTERVAL_SECONDS=60 ./scripts/poll_draft_day.sh
```

You can also run the equivalent commands directly:
```bash
cd python_app
python3 main.py sync-prospects --year 2026
python3 main.py seed-draft-order --year 2026 --csv ../examples/draft_order_seed_2026.csv
python3 main.py generate-predictions --year 2026 --top-n 5 --max-pick 50
python3 main.py live-monitor --year 2026
```

## 7. Telegram config
Set:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Draft pick messages are de-duplicated with `telegram_events_sent`.

### Test flow
Confirm delivery before relying on it during the live draft — this sends a
one-off message without touching the draft board:
```bash
make test-telegram
# or
./scripts/test_telegram.sh "custom message"
# or
cd python_app && python3 main.py test-telegram --message "custom message"
```
If credentials are missing, this fails fast with a clear error instead of
silently no-op'ing, so you'll know before draft day whether alerts will fire.

## 8. Risk areas
- baseballr may lag MLB’s live site by some minutes
- official draft order CSV placeholders must be replaced before draft day
- pick ownership can change due to compensation / traded comp-balance selections
- names may require fuzzy matching when IDs are absent

## 9. Recommended hardening
- add a second live source from official MLB draft tracker endpoints
- store HTML/JSON snapshots of each poll for debugging
- add a dashboard view of top available prospects + actual picks
- add quality checks to flag missing picks or duplicate pick numbers

## 10. Safe restart / recovery
The live monitor and its dedupe tables are designed so restarting mid-draft
is safe, but follow this sequence rather than blindly re-launching:

1. **Check status first**: `make live-monitor-status` (or
   `./scripts/live_monitor_status.sh`) shows whether a poller is still
   running, the tail of its log, and the last few picks recorded. Don't
   restart until you've read the failure in the log.
2. **Stale lock file**: `poll_draft_day.sh` writes a pidfile at
   `/tmp/mlb-draft-tracker-live-monitor-<year>.pid` and refuses to start a
   second poller for the same year while that pid is alive. If the process
   actually crashed, the status script will tell you the pidfile is stale —
   remove it (`rm -f /tmp/mlb-draft-tracker-live-monitor-<year>.pid`) before
   restarting.
3. **Restarting is idempotent**: `actual_picks` is keyed by
   `(draft_year, pick_number)` and reconciliation upserts on that key, so
   re-running `live-monitor` after a restart will not create duplicate pick
   rows.
4. **No duplicate Telegram alerts**: sends are de-duplicated via
   `telegram_events_sent` (keyed by pick number + message hash), so a
   restart will not re-alert on picks that were already successfully sent.
   A pick's message *will* re-send if its content changed since the last
   send (e.g. a correction to the player/school) — that's intentional.
5. **Automatic backoff**: `poll_draft_day.sh` stops itself after 5
   consecutive failed runs rather than looping forever against a broken
   source — treat that as a signal to investigate (`Rscript`/baseballr
   availability, network access, MLB source changes) before restarting.
6. **Back up before draft day**: the sqlite db (`data/*.db` by default) is
   a single file — copy it somewhere safe before the draft window starts so
   a bad restart or corrupted write has a known-good fallback.
