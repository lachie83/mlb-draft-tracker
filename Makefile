.DEFAULT_GOAL := help

PYTHON ?= python3
YEAR ?= 2026
DB ?=
DB_FLAG = $(if $(DB),--db $(DB),)

.PHONY: help init-db pre-draft-sync sync-prospects seed-no-r-prospects seed-draft-order \
	generate-predictions seed-mock-consensus verify-baseballr live-monitor poll-draft-day \
	live-monitor-status test-telegram dashboard

help:
	@echo "Common targets (override with YEAR=<year> DB=<path>):"
	@echo "  make pre-draft-sync      init db + seed order + sync prospects + predictions + mock consensus"
	@echo "  make poll-draft-day      run the draft-day live-monitor polling loop"
	@echo "  make live-monitor-status show poller status, recent log lines, and recent picks"
	@echo "  make test-telegram       send a one-off Telegram test message"
	@echo "  make dashboard           run the local dashboard on :8000"
	@echo "  make init-db / sync-prospects / seed-no-r-prospects / seed-draft-order"
	@echo "  make generate-predictions / seed-mock-consensus / verify-baseballr / live-monitor"

init-db:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) init-db

pre-draft-sync:
	DRAFT_YEAR=$(YEAR) DRAFT_DB=$(DB) ./scripts/pre_draft_sync.sh

sync-prospects:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) sync-prospects --year $(YEAR)

seed-no-r-prospects:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) seed-no-r-prospects --year $(YEAR)

seed-draft-order:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) seed-draft-order --year $(YEAR) --csv ../examples/draft_order_seed_$(YEAR).csv

generate-predictions:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) generate-predictions --year $(YEAR) --top-n 5 --max-pick 50

seed-mock-consensus:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) seed-mock-consensus --year $(YEAR)

verify-baseballr:
	cd python_app && $(PYTHON) main.py verify-baseballr

live-monitor:
	cd python_app && $(PYTHON) main.py $(DB_FLAG) live-monitor --year $(YEAR)

poll-draft-day:
	DRAFT_YEAR=$(YEAR) DRAFT_DB=$(DB) ./scripts/poll_draft_day.sh

live-monitor-status:
	DRAFT_YEAR=$(YEAR) DRAFT_DB=$(DB) ./scripts/live_monitor_status.sh

test-telegram:
	cd python_app && $(PYTHON) main.py test-telegram

dashboard:
	cd python_app && $(PYTHON) dashboard.py --host 0.0.0.0 --port 8000
