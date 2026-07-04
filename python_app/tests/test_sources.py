from __future__ import annotations

from pathlib import Path

from mlb_tracker.sources import EXAMPLES_DIR, normalize_prospect_row, seed_draft_slots_from_csv

from .factories import make_raw_prospect


def test_normalize_prospect_row_maps_baseballr_fields():
    raw = make_raw_prospect(
        person_id=815690,
        person_full_name="Grady Emerson",
        rank=1,
        person_primary_position_name="Shortstop",
        school_name="Fort Worth Christian (TX)",
        school_school_class="HS SR",
        is_drafted=False,
    )

    normalized = normalize_prospect_row(raw, draft_year=2026)

    assert normalized["mlb_person_id"] == 815690
    assert normalized["full_name"] == "Grady Emerson"
    assert normalized["position_name"] == "Shortstop"
    assert normalized["school_name"] == "Fort Worth Christian (TX)"
    assert normalized["school_class"] == "HS SR"
    assert normalized["is_drafted"] == 0
    assert normalized["draft_year"] == 2026
    assert normalized["source"] == "baseballr_mlb_draft_prospects"


def test_normalize_prospect_row_falls_back_when_fields_missing():
    normalized = normalize_prospect_row({}, draft_year=2026)

    assert normalized["full_name"] == "UNKNOWN"
    assert normalized["is_drafted"] == 0
    assert normalized["is_pass"] == 0
    assert normalized["mlb_person_id"] is None


def test_normalize_prospect_row_marks_drafted_players():
    raw = make_raw_prospect(is_drafted=True, pick_number=5, team_name="Seattle Mariners")

    normalized = normalize_prospect_row(raw, draft_year=2026)

    assert normalized["is_drafted"] == 1
    assert normalized["pick_number"] == 5
    assert normalized["draft_team_name"] == "Seattle Mariners"


def test_seed_draft_slots_from_csv_parses_example_seed_file():
    csv_path = Path(EXAMPLES_DIR) / "draft_order_seed_2026.csv"

    rows = seed_draft_slots_from_csv(csv_path, draft_year=2026)

    assert len(rows) > 0
    first = rows[0]
    assert first["draft_year"] == 2026
    assert first["pick_number"] > 0
    assert first["team_name"]
    assert first["round_label"]
