from __future__ import annotations

import json
from pathlib import Path

from mlb_tracker.mlb_stats_api import (
    get_on_the_clock,
    iter_picks,
    pick_to_actual_pick,
    pick_to_draft_slot,
    pick_to_raw_prospect,
    reconcile_picks_from_api,
    sync_draft_order,
)
from mlb_tracker.sources import normalize_prospect_row

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
