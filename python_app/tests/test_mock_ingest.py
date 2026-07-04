from __future__ import annotations

import math

from mlb_tracker.mock_ingest import ingest_mock_assignments

from .factories import seed_prospect


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
