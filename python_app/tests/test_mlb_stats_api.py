from __future__ import annotations

import json
from pathlib import Path

import pytest

from mlb_tracker.mlb_stats_api import (
    STATS_API_PROSPECTS_SOURCE,
    get_on_the_clock,
    iter_picks,
    pick_to_actual_pick,
    pick_to_draft_slot,
    pick_to_raw_prospect,
    reconcile_picks_from_api,
    sync_draft_order,
    sync_prospects_from_api,
)
from mlb_tracker.sources import normalize_prospect_row

from .factories import seed_actual_pick

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_iter_picks_flattens_all_rounds():
    payload = load_fixture("draft_scaffold_2026.json")
    picks = list(iter_picks(payload))
    expected = sum(len(r["picks"]) for r in payload["drafts"]["rounds"])
    assert len(picks) == expected
    assert len(picks) > 0


def test_pick_to_draft_slot_maps_undrafted_pick():
    payload = load_fixture("draft_scaffold_2026.json")
    pick = next(p for p in iter_picks(payload) if p["pickNumber"] == 1)

    slot = pick_to_draft_slot(pick, draft_year=2026)

    assert slot["draft_year"] == 2026
    assert slot["pick_number"] == 1
    assert slot["team_id"] == 145
    assert slot["team_name"] == "Chicago White Sox"
    assert slot["pick_value"] == 11350600.0
    assert slot["round_label"] == "1"
    assert slot["source"] == "mlb_stats_api"


def test_pick_to_draft_slot_handles_comp_round_labels():
    payload = load_fixture("draft_scaffold_2026.json")
    comp_pick = next(p for p in iter_picks(payload) if p["pickRound"] == "CB-A")

    slot = pick_to_draft_slot(comp_pick, draft_year=2026)

    assert slot["round_label"] == "CB-A"
    assert slot["slot_type"] == "cb-a"


def test_pick_to_raw_prospect_returns_none_for_undrafted_pick():
    payload = load_fixture("draft_scaffold_2026.json")
    pick = next(p for p in iter_picks(payload) if p["pickNumber"] == 1)

    assert pick_to_raw_prospect(pick) is None


def test_pick_to_raw_prospect_maps_drafted_pick():
    payload = load_fixture("draft_complete_2025.json")
    pick = next(p for p in iter_picks(payload) if p["pickNumber"] == 1)

    raw = pick_to_raw_prospect(pick)

    assert raw["person_id"] == 816113
    assert raw["person_full_name"] == "Eli Willits"
    assert raw["person_primary_position_name"] == "Shortstop"
    assert raw["school_name"] == "Fort Cobb-Broxton HS"
    assert raw["is_drafted"] is True

    normalized = normalize_prospect_row(raw, draft_year=2025)
    assert normalized["full_name"] == "Eli Willits"
    assert normalized["mlb_person_id"] == 816113
    assert normalized["is_drafted"] == 1


def test_pick_to_raw_prospect_handles_isPass_pick():
    payload = load_fixture("draft_complete_2025.json")
    pick = next(p for p in iter_picks(payload) if p.get("isPass"))

    assert pick_to_raw_prospect(pick) is None
    assert pick_to_actual_pick(pick, draft_year=2025) is None


def test_pick_to_actual_pick_returns_none_for_undrafted_pick():
    payload = load_fixture("draft_scaffold_2026.json")
    pick = next(p for p in iter_picks(payload) if p["pickNumber"] == 1)

    assert pick_to_actual_pick(pick, draft_year=2026) is None


def test_pick_to_actual_pick_maps_drafted_pick_with_string_numerics():
    payload = load_fixture("draft_complete_2025.json")
    pick = next(p for p in iter_picks(payload) if p["pickNumber"] == 1)

    actual = pick_to_actual_pick(pick, draft_year=2025)

    assert actual["player_name"] == "Eli Willits"
    assert actual["team_name"] == "Washington Nationals"
    assert actual["mlb_person_id"] == 816113
    assert actual["bonus_amount"] == 8200000.0
    assert actual["slot_value"] == 11075900.0
    assert isinstance(actual["bonus_amount"], float)


def test_pick_to_actual_pick_handles_missing_signing_bonus():
    payload = load_fixture("draft_complete_2025.json")
    pick = next(p for p in iter_picks(payload) if p["pickNumber"] == 50)
    assert "signingBonus" not in pick  # sanity check on the fixture itself

    actual = pick_to_actual_pick(pick, draft_year=2025)

    assert actual["player_name"] == "Angel Cervantes"
    assert actual["bonus_amount"] is None
    assert actual["slot_value"] is not None


def test_pick_to_draft_slot_handles_empty_home_and_school_objects():
    payload = load_fixture("draft_scaffold_2026.json")
    pick = next(p for p in iter_picks(payload) if p["pickNumber"] == 1)
    assert pick["home"] == {}
    assert pick["school"] == {}

    # should not raise despite the empty nested objects
    slot = pick_to_draft_slot(pick, draft_year=2026)
    assert slot is not None


def test_get_on_the_clock_returns_next_up_list():
    payload = load_fixture("draft_latest_2026.json")
    on_the_clock = get_on_the_clock(payload)

    assert len(on_the_clock) > 0
    assert on_the_clock[0]["pickNumber"] == 1
    assert on_the_clock[0]["team"]["name"] == "Chicago White Sox"


def test_get_on_the_clock_handles_missing_next_up():
    assert get_on_the_clock({}) == []


def test_sync_draft_order_upserts_every_pick_as_a_draft_slot(conn, monkeypatch):
    payload = load_fixture("draft_scaffold_2026.json")
    monkeypatch.setattr("mlb_tracker.mlb_stats_api.fetch_draft", lambda year, client=None: payload)

    rows = sync_draft_order(conn, draft_year=2026)

    expected = sum(len(r["picks"]) for r in payload["drafts"]["rounds"])
    assert len(rows) == expected
    db_count = conn.execute("SELECT COUNT(*) c FROM draft_slots WHERE draft_year = 2026").fetchone()["c"]
    assert db_count == expected


def test_reconcile_picks_from_api_creates_prospects_and_picks_for_drafted_players(conn, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    payload = load_fixture("draft_complete_2025.json")
    monkeypatch.setattr("mlb_tracker.mlb_stats_api.fetch_draft", lambda year, client=None: payload)

    new_picks = reconcile_picks_from_api(conn, draft_year=2025)

    drafted_count = sum(1 for p in iter_picks(payload) if p.get("isDrafted") and not p.get("isPass"))
    assert len(new_picks) == drafted_count
    assert conn.execute("SELECT COUNT(*) c FROM prospects WHERE draft_year = 2025").fetchone()["c"] == drafted_count
    assert conn.execute("SELECT COUNT(*) c FROM actual_picks WHERE draft_year = 2025").fetchone()["c"] == drafted_count

    pick_1 = conn.execute(
        "SELECT player_name, team_name, bonus_amount, slot_value, prospect_id FROM actual_picks "
        "WHERE draft_year = 2025 AND pick_number = 1"
    ).fetchone()
    assert pick_1["player_name"] == "Eli Willits"
    assert pick_1["prospect_id"] is not None


def test_reconcile_picks_from_api_is_idempotent_and_does_not_resend_alerts(conn, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    payload = load_fixture("draft_complete_2025.json")
    monkeypatch.setattr("mlb_tracker.mlb_stats_api.fetch_draft", lambda year, client=None: payload)

    first_pass = reconcile_picks_from_api(conn, draft_year=2025)
    second_pass = reconcile_picks_from_api(conn, draft_year=2025)

    assert len(first_pass) > 0
    assert len(second_pass) == 0
    total = conn.execute("SELECT COUNT(*) c FROM actual_picks WHERE draft_year = 2025").fetchone()["c"]
    assert total == len(first_pass)


def test_reconcile_picks_from_api_persists_all_picks_even_when_telegram_fails_for_all(conn, monkeypatch):
    # conn.commit() only happens after reconcile_picks_from_api() returns (see
    # cmd_live_monitor_api in main.py) - a notifier that raises on every send
    # is the worst case, and proves a Telegram outage can't abort the loop
    # and drop picks that were already upserted this cycle.
    payload = load_fixture("draft_complete_2025.json")
    monkeypatch.setattr("mlb_tracker.mlb_stats_api.fetch_draft", lambda year, client=None: payload)

    class FailingNotifier:
        def send(self, text):
            raise RuntimeError("simulated Telegram outage")

    new_picks = reconcile_picks_from_api(conn, draft_year=2025, notifier=FailingNotifier())

    drafted_count = sum(1 for p in iter_picks(payload) if p.get("isDrafted") and not p.get("isPass"))
    assert drafted_count > 1  # otherwise this test can't distinguish "aborted after pick 1" from "worked"
    assert len(new_picks) == drafted_count
    total = conn.execute("SELECT COUNT(*) c FROM actual_picks WHERE draft_year = 2025").fetchone()["c"]
    assert total == drafted_count


def test_sync_prospects_from_api_loads_ranked_and_unranked_prospects(conn, monkeypatch):
    payload = load_fixture("draft_prospects_2026.json")
    monkeypatch.setattr("mlb_tracker.mlb_stats_api.fetch_draft_prospects", lambda year, client=None: payload)

    result = sync_prospects_from_api(conn, draft_year=2026)

    assert result["total_synced"] == 3
    assert result["ranked_count"] == 2
    row = conn.execute(
        "SELECT source, blurb, scouting_report FROM prospects WHERE draft_year=2026 AND mlb_person_id=501"
    ).fetchone()
    assert row["source"] == STATS_API_PROSPECTS_SOURCE
    assert row["blurb"] == "Test blurb for player one."
    assert row["scouting_report"] == "Test scouting report for player one."


def test_sync_prospects_from_api_first_sync_reports_no_diff(conn, monkeypatch):
    # The very first API-sourced sync has nothing comparable to diff against
    # (prior data, if any, came from the CSV/no-R fallback's synthetic IDs) -
    # it should not report every ranked player as both new and dropped.
    payload = load_fixture("draft_prospects_2026.json")
    monkeypatch.setattr("mlb_tracker.mlb_stats_api.fetch_draft_prospects", lambda year, client=None: payload)

    result = sync_prospects_from_api(conn, draft_year=2026)

    assert result["new_entrants"] == []
    assert result["dropped"] == []
    assert result["rank_changes"] == []


def test_sync_prospects_from_api_detects_changes_on_subsequent_sync(conn, monkeypatch):
    payload = load_fixture("draft_prospects_2026.json")
    monkeypatch.setattr("mlb_tracker.mlb_stats_api.fetch_draft_prospects", lambda year, client=None: payload)
    sync_prospects_from_api(conn, draft_year=2026)

    updated = json.loads(json.dumps(payload))  # deep copy
    prospects = updated["prospects"]
    # Player Two (rank 2) drops out of the board entirely.
    prospects[:] = [p for p in prospects if p["person"]["id"] != 502]
    # Player One moves from rank 1 to rank 2.
    next(p for p in prospects if p["person"]["id"] == 501)["rank"] = 2
    # A new player enters at rank 1.
    new_player = json.loads(json.dumps(next(p for p in payload["prospects"] if p["person"]["id"] == 501)))
    new_player["person"]["id"] = 504
    new_player["person"]["fullName"] = "Player Four"
    new_player["rank"] = 1
    prospects.append(new_player)
    monkeypatch.setattr("mlb_tracker.mlb_stats_api.fetch_draft_prospects", lambda year, client=None: updated)

    result = sync_prospects_from_api(conn, draft_year=2026)

    assert [r["mlb_person_id"] for r in result["new_entrants"]] == [504]
    assert [r["mlb_person_id"] for r in result["dropped"]] == [502]
    assert result["rank_changes"] == [
        {"mlb_person_id": 501, "full_name": "Player One", "old_rank": 1, "new_rank": 2}
    ]


def test_sync_prospects_from_api_no_diff_when_nothing_changed(conn, monkeypatch):
    payload = load_fixture("draft_prospects_2026.json")
    monkeypatch.setattr("mlb_tracker.mlb_stats_api.fetch_draft_prospects", lambda year, client=None: payload)
    sync_prospects_from_api(conn, draft_year=2026)

    result = sync_prospects_from_api(conn, draft_year=2026)

    assert result["new_entrants"] == []
    assert result["dropped"] == []
    assert result["rank_changes"] == []


def test_sync_prospects_from_api_clears_stale_predictions_and_mock_picks(conn, monkeypatch):
    payload = load_fixture("draft_prospects_2026.json")
    monkeypatch.setattr("mlb_tracker.mlb_stats_api.fetch_draft_prospects", lambda year, client=None: payload)
    sync_prospects_from_api(conn, draft_year=2026)
    old_prospect_id = conn.execute(
        "SELECT prospect_id FROM prospects WHERE draft_year=2026 AND mlb_person_id=501"
    ).fetchone()["prospect_id"]
    conn.execute(
        "INSERT INTO predictions (draft_year, pick_number, team_name, prospect_id, player_name, "
        "predicted_probability, model_version, prediction_source) VALUES (2026, 1, 'Some Team', ?, 'Player One', 0.5, 'v1', 'test')",
        (old_prospect_id,),
    )
    conn.commit()

    # Would raise sqlite3.IntegrityError (FOREIGN KEY constraint failed) if
    # predictions referencing the old prospect_id weren't cleared first -
    # this is the same bug class fixed in rehearse-draft-day-cleanup.
    sync_prospects_from_api(conn, draft_year=2026)

    assert conn.execute("SELECT COUNT(*) c FROM predictions WHERE draft_year=2026").fetchone()["c"] == 0


def test_sync_prospects_from_api_preserves_actual_picks_when_draft_is_live(conn, monkeypatch):
    # If the draft has already started when this runs (a real scenario -
    # this syncs on every 6h pre_draft_sync.sh cycle regardless of draft
    # progress), actual_picks may already reference a prospect row that's
    # about to be replaced. Unlike predictions/mock_draft_picks, those rows
    # are real, irreplaceable draft results and must never be deleted -
    # only prospect_id should be cleared (see the docstring: the live
    # poller re-links it on its next cycle).
    payload = load_fixture("draft_prospects_2026.json")
    monkeypatch.setattr("mlb_tracker.mlb_stats_api.fetch_draft_prospects", lambda year, client=None: payload)
    sync_prospects_from_api(conn, draft_year=2026)
    old_prospect_id = conn.execute(
        "SELECT prospect_id FROM prospects WHERE draft_year=2026 AND mlb_person_id=501"
    ).fetchone()["prospect_id"]
    seed_actual_pick(
        conn, draft_year=2026, pick_number=1, team_name="Some Team", player_name="Player One",
        prospect_id=old_prospect_id,
    )

    # Would raise sqlite3.IntegrityError (FOREIGN KEY constraint failed) if
    # actual_picks referencing the old prospect_id weren't handled first.
    sync_prospects_from_api(conn, draft_year=2026)

    pick = conn.execute(
        "SELECT player_name, prospect_id FROM actual_picks WHERE draft_year=2026 AND pick_number=1"
    ).fetchone()
    assert pick is not None, "the real pick itself must survive, not just avoid crashing"
    assert pick["player_name"] == "Player One"
    assert pick["prospect_id"] is None


def test_sync_prospects_from_api_raises_when_endpoint_is_empty(conn, monkeypatch):
    monkeypatch.setattr(
        "mlb_tracker.mlb_stats_api.fetch_draft_prospects",
        lambda year, client=None: {"prospects": []},
    )

    with pytest.raises(RuntimeError, match="returned no prospects"):
        sync_prospects_from_api(conn, draft_year=2026)
