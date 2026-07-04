from __future__ import annotations

from mlb_tracker.db import get_best_available, get_top_prospects, row_to_dict, rows_to_dicts

from .factories import seed_draft_slot, seed_prospect


EXPECTED_TABLES = {
    "prospects",
    "draft_slots",
    "actual_picks",
    "predictions",
    "source_runs",
    "telegram_events_sent",
    "config",
}


def test_init_db_creates_expected_tables(conn):
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    assert EXPECTED_TABLES.issubset(tables)


def test_init_db_is_idempotent(db_path):
    from mlb_tracker.db import init_db

    init_db(db_path)
    init_db(db_path)


def test_upsert_prospect_inserts_new_row(conn):
    seed_prospect(conn, draft_year=2026, person_id=555, person_full_name="Jamie Rivera", rank=10)

    row = conn.execute(
        "SELECT full_name, rank, draft_year FROM prospects WHERE mlb_person_id = ?",
        (555,),
    ).fetchone()
    assert row["full_name"] == "Jamie Rivera"
    assert row["rank"] == 10
    assert row["draft_year"] == 2026


def test_upsert_prospect_updates_existing_row_on_conflict(conn):
    seed_prospect(conn, draft_year=2026, person_id=555, person_full_name="Jamie Rivera", rank=10)
    seed_prospect(conn, draft_year=2026, person_id=555, person_full_name="Jamie Rivera", rank=3, is_drafted=True)

    rows = conn.execute(
        "SELECT rank, is_drafted FROM prospects WHERE mlb_person_id = ? AND draft_year = ?",
        (555, 2026),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["rank"] == 3
    assert rows[0]["is_drafted"] == 1


def test_upsert_draft_slot_inserts_new_row(conn):
    seed_draft_slot(conn, draft_year=2026, pick_number=1, team_name="Washington Nationals")

    row = conn.execute(
        "SELECT team_name, round_label FROM draft_slots WHERE draft_year = ? AND pick_number = ?",
        (2026, 1),
    ).fetchone()
    assert row["team_name"] == "Washington Nationals"


def test_upsert_draft_slot_updates_existing_row_on_conflict(conn):
    seed_draft_slot(conn, draft_year=2026, pick_number=1, team_name="Washington Nationals")
    seed_draft_slot(conn, draft_year=2026, pick_number=1, team_name="Traded To Angels")

    rows = conn.execute(
        "SELECT team_name FROM draft_slots WHERE draft_year = ? AND pick_number = ?",
        (2026, 1),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["team_name"] == "Traded To Angels"


def test_get_best_available_excludes_drafted_and_unranked(conn):
    seed_prospect(conn, person_id=1, person_full_name="Undrafted One", rank=1, is_drafted=False)
    seed_prospect(conn, person_id=2, person_full_name="Drafted Two", rank=2, is_drafted=True)
    seed_prospect(conn, person_id=3, person_full_name="Unranked Three", rank=None, is_drafted=False)

    rows = get_best_available(conn, draft_year=2026, limit=10)
    names = [row["full_name"] for row in rows]
    assert names == ["Undrafted One"]


def test_get_best_available_orders_by_rank_and_respects_limit(conn):
    seed_prospect(conn, person_id=1, person_full_name="Rank Three", rank=3)
    seed_prospect(conn, person_id=2, person_full_name="Rank One", rank=1)
    seed_prospect(conn, person_id=3, person_full_name="Rank Two", rank=2)

    rows = get_best_available(conn, draft_year=2026, limit=2)
    names = [row["full_name"] for row in rows]
    assert names == ["Rank One", "Rank Two"]


def test_get_top_prospects_includes_drafted_players(conn):
    seed_prospect(conn, person_id=1, person_full_name="Drafted One", rank=1, is_drafted=True)
    seed_prospect(conn, person_id=2, person_full_name="Undrafted Two", rank=2, is_drafted=False)

    rows = get_top_prospects(conn, draft_year=2026, limit=10)
    names = [row["full_name"] for row in rows]
    assert names == ["Drafted One", "Undrafted Two"]


def test_row_to_dict_and_rows_to_dicts(conn):
    seed_prospect(conn, person_id=1, person_full_name="Someone", rank=1)
    row = conn.execute("SELECT * FROM prospects WHERE mlb_person_id = 1").fetchone()

    assert row_to_dict(None) is None
    as_dict = row_to_dict(row)
    assert as_dict["full_name"] == "Someone"

    rows = conn.execute("SELECT * FROM prospects").fetchall()
    dicts = rows_to_dicts(rows)
    assert len(dicts) == 1
    assert dicts[0]["full_name"] == "Someone"
