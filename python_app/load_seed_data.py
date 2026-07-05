from __future__ import annotations

from pathlib import Path

from mlb_tracker.db import DEFAULT_DB_PATH, get_connection, init_db, insert_or_replace_predictions, upsert_draft_slot, upsert_prospect
from mlb_tracker.mock_ingest import generate_mock_consensus_predictions, ingest_real_mock_draft_picks
from mlb_tracker.no_r_ingest import build_no_r_seed
from mlb_tracker.predictions import generate_predictions
from mlb_tracker.sources import normalize_prospect_row, seed_draft_slots_from_csv

ROOT = Path(__file__).resolve().parents[1]
SEED_ORDER = ROOT / 'examples' / 'draft_order_seed_2026.csv'


def main():
    init_db(DEFAULT_DB_PATH)
    conn = get_connection(DEFAULT_DB_PATH)

    for raw in build_no_r_seed():
        upsert_prospect(conn, normalize_prospect_row(raw, 2026))

    for row in seed_draft_slots_from_csv(SEED_ORDER, 2026):
        upsert_draft_slot(conn, row)

    heuristic = generate_predictions(conn, draft_year=2026, top_n_per_pick=5, max_pick=20)
    insert_or_replace_predictions(conn, heuristic, 2026, 'heuristic_v1')

    ingest_real_mock_draft_picks(conn, draft_year=2026)
    mock_rows = generate_mock_consensus_predictions(conn, draft_year=2026, top_n_per_pick=5)
    insert_or_replace_predictions(conn, mock_rows, 2026, 'mock_consensus_v2')

    conn.commit()
    conn.close()
    print('Loaded no-R seed data into SQLite DB')


if __name__ == '__main__':
    main()
