from __future__ import annotations

from pathlib import Path

from mlb_tracker.sources import (
    EXAMPLES_DIR,
    classify_school_class,
    expand_position_name,
    normalize_prospect_row,
    parse_feet_inches_height,
    seed_draft_slots_from_csv,
    seed_prospects_from_csv,
)

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


def test_expand_position_name_maps_single_abbreviations():
    assert expand_position_name("SS") == "Shortstop"
    assert expand_position_name("RHP") == "Pitcher"
    assert expand_position_name("LHP") == "Pitcher"


def test_expand_position_name_maps_infield_combos():
    assert expand_position_name("2B/3B") == "Infield"
    assert expand_position_name("SS/2B") == "Infield"


def test_expand_position_name_maps_pitcher_hitter_combos_to_two_way():
    assert expand_position_name("LHP/OF") == "Two-Way Player"
    assert expand_position_name("RHP/SS") == "Two-Way Player"


def test_expand_position_name_falls_back_to_first_component():
    assert expand_position_name("OF/C") == "Outfield"
    assert expand_position_name("C/1B") == "Catcher"


def test_classify_school_class_detects_high_school_by_state_suffix():
    assert classify_school_class("Fort Worth Christian (TX)") == "HS"
    assert classify_school_class("Upper Canada (ON)") == "HS"


def test_classify_school_class_detects_junior_college():
    assert classify_school_class("McLennan CC") == "JC"


def test_classify_school_class_defaults_to_four_year_college():
    assert classify_school_class("UCLA") == "4YR"
    assert classify_school_class("Georgia Tech") == "4YR"


def test_parse_feet_inches_height_extracts_height_and_weight():
    height, weight = parse_feet_inches_height("6' 3\" / 185 lbs")
    assert height == "6-3"
    assert weight == 185


def test_parse_feet_inches_height_handles_unparseable_input():
    height, weight = parse_feet_inches_height("not a height")
    assert height is None
    assert weight is None


def test_seed_prospects_from_csv_parses_example_seed_file(tmp_path):
    csv_path = tmp_path / "prospects.csv"
    csv_path.write_text(
        "rank,full_name,position_abbreviation,school_name,age,height,weight,bats,throws\n"
        "1,Grady Emerson,SS,Fort Worth Christian (TX),18,6-3,185,L,R\n"
        "2,Roch Cholowsky,SS,UCLA,21,6-2,202,R,R\n"
        "16,Jared Grindlinger,LHP/OF,Huntington Beach (CA),17,6-3,190,L,L\n"
    )

    rows = seed_prospects_from_csv(csv_path, draft_year=2026)

    assert len(rows) == 3
    by_rank = {row["rank"]: row for row in rows}

    first = by_rank[1]
    assert first["full_name"] == "Grady Emerson"
    assert first["position_name"] == "Shortstop"
    assert first["school_name"] == "Fort Worth Christian (TX)"
    assert first["school_class"] == "HS"
    assert first["draft_year"] == 2026
    assert first["is_drafted"] == 0
    assert first["source"] == "mlb_pipeline_draft_prospects_manual_csv"

    two_way = by_rank[16]
    assert two_way["position_name"] == "Two-Way Player"


def test_seed_prospects_from_csv_generates_unique_synthetic_person_ids(tmp_path):
    csv_path = tmp_path / "prospects.csv"
    csv_path.write_text(
        "rank,full_name,position_abbreviation,school_name,age,height,weight,bats,throws\n"
        "1,Player One,SS,School A,18,6-0,180,R,R\n"
        "2,Player Two,OF,School B,19,6-1,190,L,L\n"
    )

    rows = seed_prospects_from_csv(csv_path, draft_year=2026, id_base=100000)

    ids = [row["mlb_person_id"] for row in rows]
    assert ids == [100001, 100002]
    assert len(set(ids)) == len(ids)


def test_seed_prospects_from_csv_parses_full_top_250_example_seed_file():
    csv_path = Path(EXAMPLES_DIR) / "prospects_top250_seed_2026.csv"

    rows = seed_prospects_from_csv(csv_path, draft_year=2026)

    assert len(rows) == 250
    ranks = sorted(row["rank"] for row in rows)
    assert ranks == list(range(1, 251))
    assert len({row["mlb_person_id"] for row in rows}) == 250
    assert len({row["full_name"] for row in rows}) == 250
