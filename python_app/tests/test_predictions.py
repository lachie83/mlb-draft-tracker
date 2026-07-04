from __future__ import annotations

import math

from mlb_tracker.predictions import (
    generate_predictions,
    normalize_probabilities,
    score_prospect,
    team_fit_score,
)

from .factories import seed_draft_slot, seed_prospect


def test_score_prospect_handles_missing_and_zero_rank():
    assert score_prospect(None) == 0.01
    assert score_prospect(0) == 0.01
    assert score_prospect(-5) == 0.01


def test_score_prospect_decreases_as_rank_worsens():
    assert score_prospect(1) == 1.0
    assert score_prospect(4) == 0.5
    assert score_prospect(1) > score_prospect(4) > score_prospect(100)


def test_team_fit_score_rewards_premium_positions_and_hs_bump():
    shortstop_hs = team_fit_score("Some Team", "Shortstop", "HS SR")
    shortstop_college = team_fit_score("Some Team", "Shortstop", "4YR SR")
    other_position = team_fit_score("Some Team", "First Base", None)

    assert shortstop_hs > shortstop_college
    assert shortstop_college > other_position


def test_normalize_probabilities_sums_to_one_and_sorts_descending():
    rows = [
        {"combined_score": 0.4},
        {"combined_score": 0.1},
        {"combined_score": 0.5},
    ]
    normalized = normalize_probabilities(rows)

    total = sum(r["predicted_probability"] for r in normalized)
    assert math.isclose(total, 1.0, rel_tol=1e-9)
    assert [r["combined_score"] for r in normalized] == [0.5, 0.4, 0.1]


def test_normalize_probabilities_floors_non_positive_scores():
    rows = [{"combined_score": 0.0}, {"combined_score": -1.0}]
    normalized = normalize_probabilities(rows)

    assert all(r["predicted_probability"] > 0 for r in normalized)


def test_generate_predictions_covers_each_slot_and_respects_top_n(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Washington Nationals")
    seed_draft_slot(conn, pick_number=2, team_name="Los Angeles Angels")
    # exactly top_n_per_pick prospects, so each slot's candidate pool equals the
    # returned rows and their probabilities should sum to 1.0
    for i in range(1, 4):
        seed_prospect(conn, person_id=1000 + i, person_full_name=f"Prospect {i}", rank=i)

    predictions = generate_predictions(conn, draft_year=2026, top_n_per_pick=3, max_pick=2)

    by_pick: dict[int, list[dict]] = {}
    for row in predictions:
        by_pick.setdefault(row["pick_number"], []).append(row)

    assert set(by_pick.keys()) == {1, 2}
    for pick_number, rows in by_pick.items():
        assert len(rows) == 3
        probs = [r["predicted_probability"] for r in rows]
        assert math.isclose(sum(probs), 1.0, rel_tol=1e-6)
        assert probs == sorted(probs, reverse=True)


def test_generate_predictions_ignores_already_drafted_prospects(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Washington Nationals")
    seed_prospect(conn, person_id=1, person_full_name="Drafted Player", rank=1, is_drafted=True)
    seed_prospect(conn, person_id=2, person_full_name="Available Player", rank=2, is_drafted=False)

    predictions = generate_predictions(conn, draft_year=2026, top_n_per_pick=5, max_pick=1)

    names = {row["player_name"] for row in predictions}
    assert "Drafted Player" not in names
    assert "Available Player" in names
