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
1. Initialize database
2. Sync prospects from baseballr
3. Seed official order from validated CSV / scraper
4. Generate predictions
5. Test Telegram with a dry-run message
6. Start live monitor polling loop
7. Verify first 3 picks manually against MLB.com
8. Watch for duplicates / missing picks in `telegram_events_sent`

## 5. Example commands
```bash
cd python_app
python3 main.py init-db
python3 main.py sync-prospects --year 2026
python3 main.py seed-draft-order --year 2026 --csv ../examples/draft_order_seed_2026.csv
python3 main.py generate-predictions --year 2026 --top-n 5 --max-pick 50
python3 main.py live-monitor --year 2026
```

## 6. Suggested cron entries
```cron
# daily sync
0 7 * * * cd /path/to/mlb_draft_tracker/python_app && /usr/bin/python3 main.py sync-prospects --year 2026
15 8 * * * cd /path/to/mlb_draft_tracker/python_app && /usr/bin/python3 main.py generate-predictions --year 2026 --top-n 5 --max-pick 50

# draft day live monitor (example only; adjust to actual draft schedule)
*/1 23-3 11-12 7 * cd /path/to/mlb_draft_tracker/python_app && /usr/bin/python3 main.py live-monitor --year 2026
```

## 7. Telegram config
Set:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Draft pick messages are de-duplicated with `telegram_events_sent`.

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
