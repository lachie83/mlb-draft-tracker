from __future__ import annotations

import math

from mlb_tracker.mock_ingest import generate_mock_consensus_predictions, ingest_mock_assignments, ingest_real_mock_draft_picks

from .factories import seed_mock_draft_pick, seed_prospect


def test_ingest_mock_assignments_computes_weighted_probability_and_matches_prospect(conn):
    seed_prospect(conn, person_id=1, person_full_name="Grady Emerson", rank=1)
    seed_prospect(conn, person_id=2, person_full_name="Roch Cholowsky", rank=2)

    assignments = [
        {"pick_number": 1, "team_name": "Tampa Bay Rays", "player_name": "Grady Emerson", "weight": 2.0},
        {"pick_number": 1, "team_name": "Tampa Bay Rays", "player_name": "Roch Cholowsky", "weight": 1.0},
    ]

    rows = ingest_mock_assignments(conn, assignments, draft_year=2026)

    by_name = {row["player_name"]: row for row in rows}
    assert math.isclose(by_name["Grady Emerson"]["predicted_probability"], 2 / 3)
    assert math.isclose(by_name["Roch Cholowsky"]["predicted_probability"], 1 / 3)
    assert by_name["Grady Emerson"]["mlb_person_id"] == 1
    assert by_name["Grady Emerson"]["prospect_id"] is not None


def test_ingest_mock_assignments_handles_unmatched_player_name(conn):
    seed_prospect(conn, person_id=1, person_full_name="Grady Emerson", rank=1)

    assignments = [
        {"pick_number": 5, "team_name": "Some Team", "player_name": "Nobody On Board", "weight": 1.0},
    ]

    rows = ingest_mock_assignments(conn, assignments, draft_year=2026)

    assert len(rows) == 1
    assert rows[0]["prospect_id"] is None
    assert rows[0]["mlb_person_id"] is None
    assert rows[0]["predicted_probability"] == 1.0


def test_ingest_mock_assignments_matches_player_name_case_insensitively(conn):
    seed_prospect(conn, person_id=1, person_full_name="Grady Emerson", rank=1)

    assignments = [
        {"pick_number": 1, "team_name": "Some Team", "player_name": "grady emerson", "weight": 1.0},
    ]

    rows = ingest_mock_assignments(conn, assignments, draft_year=2026)

    assert rows[0]["mlb_person_id"] == 1


def test_ingest_real_mock_draft_picks_populates_and_matches_prospects(conn):
    seed_prospect(conn, person_id=1, person_full_name="Roch Cholowsky", rank=2)
    seed_prospect(conn, person_id=2, person_full_name="Grady Emerson", rank=1)
    seed_prospect(conn, person_id=3, person_full_name="Vahn Lackey", rank=3)

    rows = ingest_real_mock_draft_picks(conn, draft_year=2026)

    assert len(rows) > 0
    pick_1_rows = conn.execute(
        "SELECT player_name, mlb_person_id, source_date FROM mock_draft_picks WHERE draft_year = 2026 AND pick_number = 1"
    ).fetchall()
    names = {r["player_name"] for r in pick_1_rows}
    # July 2 mock gave a 3-way split for pick 1, June 25 mock had a single pick,
    # so all three top-3 names should appear somewhere in pick 1's rows.
    assert {"Roch Cholowsky", "Grady Emerson", "Vahn Lackey"} <= names
    matched = {r["player_name"]: r["mlb_person_id"] for r in pick_1_rows}
    assert matched["Roch Cholowsky"] == 1
    assert matched["Grady Emerson"] == 2
    assert matched["Vahn Lackey"] == 3


def test_ingest_real_mock_draft_picks_is_idempotent(conn):
    ingest_real_mock_draft_picks(conn, draft_year=2026)
    total_after_first = conn.execute("SELECT COUNT(*) c FROM mock_draft_picks WHERE draft_year = 2026").fetchone()["c"]

    ingest_real_mock_draft_picks(conn, draft_year=2026)
    total_after_second = conn.execute("SELECT COUNT(*) c FROM mock_draft_picks WHERE draft_year = 2026").fetchone()["c"]

    assert total_after_first > 0
    assert total_after_first == total_after_second


def test_ingest_real_mock_draft_picks_falls_back_to_board_rank_on_name_mismatch(conn):
    # The July 2 source article names this player "Cameron Borthwick" at
    # board rank 43, but the board itself (and the June 25 mock) call him
    # something else - simulate that name mismatch here.
    seed_prospect(conn, person_id=99, person_full_name="Coleman Borthwick", rank=43)

    ingest_real_mock_draft_picks(conn, draft_year=2026)

    row = conn.execute(
        "SELECT mlb_person_id FROM mock_draft_picks WHERE draft_year = 2026 AND player_name = 'Cameron Borthwick'"
    ).fetchone()
    assert row is not None
    assert row["mlb_person_id"] == 99


def test_generate_mock_consensus_predictions_aggregates_same_player_across_sources(conn):
    seed_mock_draft_pick(conn, pick_number=1, player_name="Roch Cholowsky", team_name="Chicago White Sox", source_name="Source A", source_date="2026-06-01", weight=1.0)
    seed_mock_draft_pick(conn, pick_number=1, player_name="Roch Cholowsky", team_name="Chicago White Sox", source_name="Source B", source_date="2026-07-01", weight=0.75)
    seed_mock_draft_pick(conn, pick_number=1, player_name="Grady Emerson", team_name="Chicago White Sox", source_name="Source B", source_date="2026-07-01", weight=0.25)

    rows = generate_mock_consensus_predictions(conn, draft_year=2026, top_n_per_pick=5)

    by_name = {r["player_name"]: r for r in rows}
    assert len(rows) == 2
    assert by_name["Roch Cholowsky"]["mock_score"] == 1.75
    assert math.isclose(by_name["Roch Cholowsky"]["predicted_probability"], 1.75 / 2.0)
    assert math.isclose(by_name["Grady Emerson"]["predicted_probability"], 0.25 / 2.0)
    assert "Source A" in by_name["Roch Cholowsky"]["prediction_source"]
    assert "Source B" in by_name["Roch Cholowsky"]["prediction_source"]
    assert by_name["Roch Cholowsky"]["model_version"] == "mock_consensus_v2"


def test_generate_mock_consensus_predictions_respects_top_n_per_pick(conn):
    for i in range(10):
        seed_mock_draft_pick(conn, pick_number=1, player_name=f"Player {i}", weight=float(10 - i), source_date="2026-06-01")

    rows = generate_mock_consensus_predictions(conn, draft_year=2026, top_n_per_pick=3)

    assert len(rows) == 3
    probs = [r["predicted_probability"] for r in rows]
    assert probs == sorted(probs, reverse=True)


def test_generate_mock_consensus_predictions_covers_multiple_picks(conn):
    seed_mock_draft_pick(conn, pick_number=1, player_name="Player A", weight=1.0)
    seed_mock_draft_pick(conn, pick_number=2, player_name="Player B", weight=1.0)

    rows = generate_mock_consensus_predictions(conn, draft_year=2026)

    pick_numbers = {r["pick_number"] for r in rows}
    assert pick_numbers == {1, 2}
