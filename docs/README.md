# MLB Draft 2026 Tracker

This project creates a local SQL database for:
- draft prospects
- official draft pick slots
- live pick outcomes
- prediction probabilities
- Telegram delivery state

## Folders
- `sql/` — schema
- `python_app/` — Python CLI app
- `r_prototype/` — baseballr-based R scripts
- `examples/` — seed CSVs and examples
- `docs/` — architecture and runbooks

## Quick start
```bash
cd python_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 main.py init-db
python3 main.py seed-draft-order --csv ../examples/draft_order_seed_2026.csv
python3 main.py seed-no-r-prospects --year 2026
python3 main.py generate-predictions --year 2026
python3 main.py seed-mock-consensus --year 2026
python3 dashboard.py --host 0.0.0.0 --port 8000
```

## Live-source modes
### Preferred mode
- `python3 main.py sync-prospects --year 2026`
- requires `Rscript` + `baseballr`, `DBI`, `RSQLite`, `jsonlite`
- verify setup: `python3 main.py verify-baseballr`

### No-R fallback mode
- `python3 main.py seed-no-r-prospects --year 2026`
- uses MLB Pipeline page scraping plus curated fallback names
- works in environments without R installed

## Notes
- The included draft-order CSV is a scaffold. Early first-round rows are better grounded; compensation and special-round rows still need final verification.
- `baseballr::mlb_draft_prospects()` remains the preferred source for live prospect-to-pick mapping.
- Telegram alerting requires environment variables or sandbox-integrated delivery.
