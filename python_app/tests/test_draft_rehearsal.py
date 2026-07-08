from __future__ import annotations

import json
from pathlib import Path

import pytest

from mlb_tracker.db import get_best_available, mark_event_sent
from mlb_tracker.draft_rehearsal import ReplayClient, cleanup_rehearsal_data, rehearse_draft_day
from mlb_tracker.mlb_stats_api import iter_picks

from .factories import seed_actual_pick, seed_draft_slot, seed_prospect

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_replay_client_reveals_picks_in_batches():
    payload = load_fixture("draft_complete_2025.json")
    client = ReplayClient(payload, batch_size=2, max_reveal=5)

    first = client.get_json("ignored")
    assert len(list(iter_picks(first))) == 2
    assert not client.is_finished()

    second = client.get_json("ignored")
    assert len(list(iter_picks(second))) == 4

    third = client.get_json("ignored")
    assert len(list(iter_picks(third))) == 5  # clamped to max_reveal
    assert client.is_finished()


def test_replay_client_reveals_picks_in_pick_number_order():
    payload = load_fixture("draft_complete_2025.json")
    client = ReplayClient(payload, batch_size=1)
    revealed = client.get_json("ignored")
    pick = next(iter_picks(revealed))
    assert pick["pickNumber"] == 1  # the lowest pick number, not fixture file order


def test_rehearse_draft_day_seeds_full_schedule_upfront(conn, monkeypatch):
    payload = load_fixture("draft_complete_2025.json")
    monkeypatch.setattr("mlb_tracker.draft_rehearsal.fetch_draft", lambda year, client=None: payload)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    rehearse_draft_day(conn, source_year=2025, target_year=9999, picks=2, batch_size=1, delay_seconds=0)

    expected_slots = sum(len(r["picks"]) for r in payload["drafts"]["rounds"])
    slot_count = conn.execute("SELECT COUNT(*) c FROM draft_slots WHERE draft_year = 9999").fetchone()["c"]
    assert slot_count == expected_slots


def test_rehearse_draft_day_trickles_picks_gradually(conn, monkeypatch):
    payload = load_fixture("draft_complete_2025.json")
    monkeypatch.setattr("mlb_tracker.draft_rehearsal.fetch_draft", lambda year, client=None: payload)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    ticks = []
    total = rehearse_draft_day(
        conn,
        source_year=2025,
        target_year=9999,
        picks=3,
        batch_size=1,
        delay_seconds=0,
        on_tick=lambda new_picks, revealed, total: ticks.append((len(new_picks), revealed, total)),
    )

    assert total == 3
    assert len(ticks) == 3
    assert [t[1] for t in ticks] == [1, 2, 3]
    actual_picks = conn.execute("SELECT COUNT(*) c FROM actual_picks WHERE draft_year = 9999").fetchone()["c"]
    assert actual_picks == 3


def test_rehearse_draft_day_does_not_touch_other_years(conn, monkeypatch):
    payload = load_fixture("draft_complete_2025.json")
    monkeypatch.setattr("mlb_tracker.draft_rehearsal.fetch_draft", lambda year, client=None: payload)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    rehearse_draft_day(conn, source_year=2025, target_year=9999, picks=2, batch_size=1, delay_seconds=0)

    assert conn.execute("SELECT COUNT(*) c FROM actual_picks WHERE draft_year = 2025").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM draft_slots WHERE draft_year = 2025").fetchone()["c"] == 0


def test_rehearse_draft_day_seeds_full_board_so_best_available_works(conn, monkeypatch):
    payload = load_fixture("draft_complete_2025.json")
    monkeypatch.setattr("mlb_tracker.draft_rehearsal.fetch_draft", lambda year, client=None: payload)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    rehearse_draft_day(conn, source_year=2025, target_year=9999, picks=1, batch_size=1, delay_seconds=0)

    total_players = sum(1 for p in iter_picks(payload) if p.get("person") and not p.get("isPass"))
    still_undrafted = conn.execute(
        "SELECT COUNT(*) c FROM prospects WHERE draft_year = 9999 AND COALESCE(is_drafted, 0) = 0"
    ).fetchone()["c"]
    # exactly one pick has been revealed as drafted; everyone else should
    # still show up as available on the board
    assert still_undrafted == total_players - 1

    best = get_best_available(conn, draft_year=9999, limit=3)
    assert len(best) > 0
    assert all(row["is_drafted"] in (0, None) for row in best)


def test_rehearse_draft_day_flips_a_players_prospect_row_when_drafted(conn, monkeypatch):
    payload = load_fixture("draft_complete_2025.json")
    monkeypatch.setattr("mlb_tracker.draft_rehearsal.fetch_draft", lambda year, client=None: payload)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    rehearse_draft_day(conn, source_year=2025, target_year=9999, picks=1, batch_size=1, delay_seconds=0)

    row = conn.execute(
        "SELECT is_drafted FROM prospects WHERE draft_year = 9999 AND full_name = 'Eli Willits'"
    ).fetchone()
    assert row is not None
    assert row["is_drafted"] == 1


def seed_rehearsal_rows(conn, year: int) -> None:
    seed_draft_slot(conn, draft_year=year, pick_number=1, team_name="Team A")
    seed_prospect(conn, draft_year=year, person_id=1, person_full_name="Some Prospect")
    # actual_picks.prospect_id is a real FOREIGN KEY on prospects.prospect_id
    # (enforced - get_connection always sets PRAGMA foreign_keys = ON), so
    # link it here for real rather than leaving it NULL - a NULL FK is
    # trivially deletable in any order and would silently hide a real
    # ordering bug in cleanup_rehearsal_data (as it did before this test
    # was written this way).
    prospect_id = conn.execute(
        "SELECT prospect_id FROM prospects WHERE draft_year = ? AND full_name = ?",
        (year, "Some Prospect"),
    ).fetchone()["prospect_id"]
    seed_actual_pick(
        conn, draft_year=year, pick_number=1, team_name="Team A", player_name="Some Prospect",
        prospect_id=prospect_id,
    )
    mark_event_sent(conn, f"draft_pick:{year}:1", "hash", 1, "message text")
    conn.commit()


def test_cleanup_rehearsal_data_deletes_only_the_target_year(conn):
    seed_rehearsal_rows(conn, 9999)
    seed_rehearsal_rows(conn, 2026)

    cleanup_rehearsal_data(conn, year=9999)

    for table in ("draft_slots", "prospects", "actual_picks"):
        assert conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE draft_year = 9999").fetchone()["c"] == 0
        assert conn.execute(f"SELECT COUNT(*) c FROM {table} WHERE draft_year = 2026").fetchone()["c"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM telegram_events_sent WHERE event_key = 'draft_pick:9999:1'"
    ).fetchone()["c"] == 0
    assert conn.execute(
        "SELECT COUNT(*) c FROM telegram_events_sent WHERE event_key = 'draft_pick:2026:1'"
    ).fetchone()["c"] == 1


def test_cleanup_rehearsal_data_returns_rowcounts_per_table(conn):
    seed_rehearsal_rows(conn, 9999)

    deleted = cleanup_rehearsal_data(conn, year=9999)

    assert deleted == {
        "actual_picks": 1,
        "predictions": 0,
        "mock_draft_picks": 0,
        "draft_slots": 1,
        "prospects": 1,
        "telegram_events_sent": 1,
    }


@pytest.mark.parametrize("protected_year", [2025, 2026])
def test_cleanup_rehearsal_data_refuses_real_draft_years(conn, protected_year):
    with pytest.raises(ValueError, match="real draft year"):
        cleanup_rehearsal_data(conn, year=protected_year)
