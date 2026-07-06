# MLB Draft 2026 Tracker — Operations Plan

## TL;DR — what to actually run
Everything below is reference material; day-to-day you only need these few
commands. All accept `YEAR=<year>` / `DB=<path>` overrides.

**Anytime before draft day** (repeatable, safe to re-run):
```bash
make pre-draft-sync       # refresh board, order, and predictions
make test-telegram        # confirm a real alert reaches your phone
make rehearse-draft-day   # replay a real past draft through the whole pipeline
                           # (order sync -> live pick detection -> Telegram -> dashboard),
                           # entirely in a separate rehearsal database - see §4a
```

**On draft day**, in order:
```bash
make pre-draft-sync    # one final refresh before things start
make poll-draft-day    # start this and leave it running for the whole draft
make dashboard         # in another terminal/tab, to watch live
```
That's the whole day. Everything else in this doc is either what those
three commands do under the hood, a fallback path, or a recovery procedure
for when something goes wrong.

## 1. Objective
Run a local SQLite-backed tracker that:
- stores the 2026 draft prospect board
- stores the official draft order / pick slots
- updates actual picks during the draft
- generates Telegram notifications for each new pick
- maintains heuristic predictions for likely player/team outcomes

## 2. Components
- `python_app/main.py` — main CLI
- `python_app/mlb_tracker/mlb_stats_api.py` — official MLB Stats API client:
  draft order scaffold + live pick reconciliation, no R dependency
- `sql/schema.sql` — SQLite schema
- `r_prototype/fetch_prospects.R` — R/baseballr sync prototype (prospect board only now)
- `r_prototype/live_monitor.R` — R/baseballr live pick sync prototype (legacy; see `live-monitor-api`)
- Telegram delivery via bot token + chat id env vars
- `Makefile` + `scripts/` — automation helpers for the workflows below (see §5a)

## 3. Suggested schedules
### Pre-draft (now through draft week)
- `sync-draft-order-api`: daily (order rarely changes, but comp-round assignments can)
- `sync-prospects` (or the no-R fallback): daily at 7:00 UTC
- `generate-predictions`: daily at 8:00 UTC

### Draft week
- `sync-draft-order-api`: every 6 hours
- `sync-prospects`: every 6 hours
- `generate-predictions`: every 4 hours

### Draft live window
Per MLB's own guidance, poll the lightweight `/latest`-backed
`on-the-clock-api` frequently and do full reconciliation less often:
- `on-the-clock-api`: every 15-30 sec for "who's up now" display
- `live-monitor-api`: every 60 sec (does a full reconcile each call; safe to
  run this alone without `on-the-clock-api` if you don't need the faster
  "who's up next" signal separately)
- optional `generate-predictions`: every 15 min if ingesting fresh mock intel

Only poll this frequently during the actual draft windows (see the 2026
schedule in the README's Predictions/Modes sections); a slow hourly refresh
is enough outside of live picking.

## 4. Draft-day runbook
1. `make pre-draft-sync` — initialize database, sync official order via the
   MLB Stats API, sync prospects (baseballr or fallback), generate predictions
2. `make test-telegram` — confirm Telegram delivery with a dry-run message
3. `make poll-draft-day` — start the live-monitor polling loop (uses
   `live-monitor-api` by default — no R dependency)
4. Verify first 3 picks manually against MLB.com
5. `make live-monitor-status` — spot-check the poller, log, and recorded picks at any point
6. Watch for duplicates / missing picks in `telegram_events_sent`

## 4a. Rehearse before draft day
`rehearse-draft-day` replays a real, completed past draft (2025 by default)
through the *exact same* code path used on the real day —
`mlb_stats_api.reconcile_picks_from_api` — revealing picks a few at a time
instead of all at once. This is the way to test the whole pipeline (draft
order seeding, live pick detection, Telegram alerts, dashboard rendering)
before it matters, using real data instead of guesswork.

```bash
make rehearse-draft-day                          # 10 picks, 5s apart, default
make rehearse-draft-day PICKS=30 DELAY=2         # a longer/faster rehearsal
make rehearse-draft-day PICKS=615 DELAY=0        # replay the entire 2025 draft instantly
```

While it's running, watch it live in another terminal:
```bash
cd python_app && python3 dashboard.py
# then open http://localhost:8000/?year=9999
```
(Year 9999 doesn't have a quick-toggle button in the dashboard header, but
the URL works directly — it's a sentinel year used only for rehearsals.)

**Safety**: this writes to a dedicated `data/rehearsal.db` by default
(`REHEARSAL_DB=` to change it) and tags all simulated data with
`draft_year=9999` by default, so it can never mix with your real 2026 data
even if you point it at the same database file. If Telegram is configured,
it **will** send real alerts — one per simulated pick — so start with a
small `PICKS` count (the default is 10) the first time.

To rehearse again from scratch: `rm data/rehearsal.db` first (otherwise
already-simulated picks are skipped, since reconciliation is idempotent —
which is itself worth confirming once, by running the same command twice
in a row and seeing the second pass report 0 new picks).

## 5. Example commands
```bash
cd python_app
python3 main.py init-db
python3 main.py sync-draft-order-api --year 2026
python3 main.py sync-prospects --year 2026
python3 main.py generate-predictions --year 2026 --top-n 5 --max-pick 50
python3 main.py live-monitor-api --year 2026
```

## 5a. Scripts and make targets
The commands above are wrapped by `Makefile` targets and `scripts/*.sh` so
draft-day operation doesn't require remembering flag combinations. All
targets accept `YEAR=<year>` (default `2026`) and `DB=<path>` (default: the
app's built-in db path) overrides, e.g. `make pre-draft-sync YEAR=2025`.

| Command | What it does |
| --- | --- |
| `make pre-draft-sync` | `scripts/pre_draft_sync.sh` — init db, sync draft order via the MLB Stats API, sync prospects (baseballr if its live call succeeds, else CSV/no-R fallback — this now recovers even when baseballr is installed but its live call fails), generate heuristic predictions, and (2026 only) seed real mock draft picks + consensus predictions. Idempotent — safe to re-run on a schedule. |
| `make poll-draft-day` | `scripts/poll_draft_day.sh` — runs `live-monitor-api` on a loop by default (override with `LIVE_MONITOR_CMD=live-monitor` for the legacy baseballr path), default every 60s via `POLL_INTERVAL_SECONDS`. Refuses to start a second poller for the same year, and stops itself after 5 consecutive failures instead of looping forever. |
| `make live-monitor-status` | `scripts/live_monitor_status.sh` — shows whether a poller is running, the tail of its log, and the most recently recorded picks. Use this before restarting after a crash. |
| `make test-telegram` | Sends a one-off Telegram message via `main.py test-telegram` to confirm delivery. See §7. |
| `make seed-mock-drafts` / `seed-mock-consensus` | Loads real, dated mock draft picks into `mock_draft_picks`, then aggregates them into `mock_consensus_v2` predictions (`seed-mock-consensus` runs `seed-mock-drafts` first automatically). See the README's Predictions section for how these are sourced and combined. |
| `make sync-draft-order-api` / `live-monitor-api` / `on-the-clock-api` | The MLB Stats API path (no R dependency): full draft scaffold, full reconciliation + Telegram alerts, and the lightweight "who's on the clock" endpoint, respectively. See the README's Modes section. |
| `make rehearse-draft-day` | Replays a real past draft through the full pipeline into a dedicated rehearsal database (override with `PICKS=`, `BATCH_SIZE=`, `DELAY=`, `REHEARSAL_DB=`). See §4a. |
| `make init-db` / `sync-prospects` / `seed-no-r-prospects` / `seed-prospects-csv` / `seed-draft-order` / `generate-predictions` / `verify-baseballr` / `live-monitor` | Thin wrappers around the matching `main.py` subcommand. `seed-draft-order` (CSV) and `live-monitor` (baseballr) are the legacy paths, superseded by `sync-draft-order-api` / `live-monitor-api` above. `seed-prospects-csv` loads the full top-250 board from `examples/prospects_top250_seed_2026.csv` when baseballr is unavailable. |
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
python3 main.py sync-draft-order-api --year 2026
python3 main.py sync-prospects --year 2026
python3 main.py generate-predictions --year 2026 --top-n 5 --max-pick 50
python3 main.py live-monitor-api --year 2026
```

## 7. Telegram config
### One-time setup
1. **Create a bot**: in Telegram, message [@BotFather](https://t.me/BotFather)
   and send `/newbot`. Follow the prompts (pick a display name, then a
   username ending in `bot`). BotFather replies with an API token that
   looks like `123456789:AAExampleTokenTextGoesHere` — this is
   `TELEGRAM_BOT_TOKEN`.
2. **Get a chat id**:
   - *Personal chat*: open a chat with your new bot and send it any
     message first (bots can't message you until you've messaged them).
     Then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a
     browser (substitute your real token) and find `"chat":{"id":...}` in
     the JSON response — that number is `TELEGRAM_CHAT_ID`. (Alternatively,
     message a helper bot like `@userinfobot` to get your own id directly.)
   - *Group chat* (e.g. a league draft-day group): add your bot to the
     group, send any message in the group, then hit the same `getUpdates`
     URL — the group's chat id will be a negative number.
3. **Set the env vars**: copy `.env.example` to `.env` and fill in both
   values (or export them in your shell). Never commit `.env` — it's
   already gitignored.
   ```bash
   cp .env.example .env
   # edit .env:
   # TELEGRAM_BOT_TOKEN=123456789:AAExampleTokenTextGoesHere
   # TELEGRAM_CHAT_ID=987654321
   ```
4. **Verify**: `make test-telegram` (see below). You should see the test
   message appear in Telegram within a few seconds.

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
- baseballr's live call can fail even when R/packages are installed
  correctly (hit in production: `mlb_draft_prospects(year = 2026)` erroring
  with `object 'draft_prospects' not found`) — this only affects the
  prospect board now, since draft order/live picks use the Stats API
  directly; `pre_draft_sync.sh` falls back automatically when it happens
- pick ownership can change due to compensation / traded comp-balance selections
- names may require fuzzy matching when IDs are absent (mainly relevant to
  the baseballr/no-R prospect-board paths; the Stats API path matches on
  the numeric `person.id` directly)
- the MLB Stats API's `/latest` endpoint hasn't been observed against an
  actually-live draft yet (verified pre-draft only) — re-check its exact
  shape once a draft is in progress before relying on more of it than
  `nextUp` (see `mlb_stats_api.get_on_the_clock`)

## 9. Recommended hardening
- ~~add a second live source from official MLB draft tracker endpoints~~ done — see `mlb_tracker/mlb_stats_api.py`
- store HTML/JSON snapshots of each poll for debugging
- add a dashboard view of top available prospects + actual picks
- add quality checks to flag missing picks or duplicate pick numbers
- fully retire the R/baseballr path once a live source covers the pre-draft prospect board too

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
   source — treat that as a signal to investigate (network access to
   statsapi.mlb.com, MLB source changes, or `Rscript`/baseballr
   availability if you've overridden `LIVE_MONITOR_CMD=live-monitor`)
   before restarting.
6. **Back up before draft day**: the sqlite db (`data/*.db` by default) is
   a single file — copy it somewhere safe before the draft window starts so
   a bad restart or corrupted write has a known-good fallback.
