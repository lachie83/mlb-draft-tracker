.DEFAULT_GOAL := help

PYTHON ?= python3
YEAR ?= 2026
DB ?=
DB_FLAG = $(if $(DB),--db $(DB),)

REHEARSAL_DB ?= ../data/rehearsal.db
REHEARSAL_YEAR ?= 9999
PICKS ?= 10
BATCH_SIZE ?= 1
DELAY ?= 5

.PHONY: help init-db pre-draft-sync sync-prospects sync-prospects-api seed-no-r-prospects seed-prospects-csv seed-draft-order \
	sync-draft-order-api generate-predictions seed-mock-drafts seed-mock-consensus verify-baseballr \
	live-monitor live-monitor-api on-the-clock-api rehearse-draft-day rehearse-draft-day-cleanup \
	poll-draft-day live-monitor-status test-telegram dashboard

help:
	@echo "Common targets (override with YEAR=<year> DB=<path>):"
	@echo "  make pre-draft-sync            init db + sync order (API) + sync prospects + predictions + mock consensus"
	@echo "  make sync-prospects-api        replace the prospect board with a live pull from statsapi.mlb.com (rank + scouting text)"
	@echo "  make rehearse-draft-day        replay a real past draft to test the whole pipeline + Telegram + dashboard"
	@echo "  make rehearse-draft-day-cleanup  delete a rehearsal's rows (default draft_year=9999) so it can be rerun"
	@echo "  make poll-draft-day            run the draft-day live-monitor polling loop (MLB Stats API, no R needed)"
	@echo "  make live-monitor-status       show poller status, recent log lines, and recent picks"
	@echo "  make on-the-clock-api          print who's on the clock right now via the MLB Stats API"
	@echo "  make test-telegram             send a one-off Telegram test message"
	@echo "  make dashboard                 run the local dashboard on :8000"
	@echo "  make init-db / sync-prospects / seed-no-r-prospects / seed-prospects-csv / seed-draft-order / sync-draft-order-api"
	@echo "  make generate-predictions / seed-mock-drafts / seed-mock-consensus / verify-baseballr / live-monitor / live-monitor-api"

init-db:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) init-db

pre-draft-sync:
	DRAFT_YEAR=$(YEAR) DRAFT_DB=$(DB) ./scripts/pre_draft_sync.sh

sync-prospects:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) sync-prospects --year $(YEAR)

seed-no-r-prospects:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) seed-no-r-prospects --year $(YEAR)

seed-prospects-csv:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) seed-prospects-csv --year $(YEAR)

seed-draft-order:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) seed-draft-order --year $(YEAR) --csv ../examples/draft_order_seed_$(YEAR).csv

sync-draft-order-api:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) sync-draft-order-api --year $(YEAR)

sync-prospects-api:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) sync-prospects-api --year $(YEAR)

generate-predictions:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) generate-predictions --year $(YEAR) --top-n 5 --max-pick 50

seed-mock-drafts:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) seed-mock-drafts --year $(YEAR)

seed-mock-consensus: seed-mock-drafts
	cd python_app && $(PYTHON) main.py $(DB_FLAG) seed-mock-consensus --year $(YEAR)

verify-baseballr:
	cd python_app && $(PYTHON) main.py verify-baseballr

live-monitor:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) live-monitor --year $(YEAR)

live-monitor-api:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) live-monitor-api --year $(YEAR)

on-the-clock-api:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) on-the-clock-api --year $(YEAR)

rehearse-draft-day:
	cd python_app && $(PYTHON) main.py --db $(REHEARSAL_DB) rehearse-draft-day \
		--picks $(PICKS) --batch-size $(BATCH_SIZE) --delay $(DELAY)

rehearse-draft-day-cleanup:
	cd python_app && $(PYTHON) main.py --db $(REHEARSAL_DB) rehearse-draft-day-cleanup --year $(REHEARSAL_YEAR)

poll-draft-day:
	DRAFT_YEAR=$(YEAR) DRAFT_DB=$(DB) ./scripts/poll_draft_day.sh

live-monitor-status:
	DRAFT_YEAR=$(YEAR) DRAFT_DB=$(DB) ./scripts/live_monitor_status.sh

test-telegram:
	cd python_app && $(PYTHON) main.py test-telegram

dashboard:
	cd python_app && $(PYTHON) dashboard.py --host 0.0.0.0 --port 8000
