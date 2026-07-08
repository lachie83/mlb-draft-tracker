from __future__ import annotations

from dashboard import describe_prospect_source, fetch_dashboard_data, fetch_latest_picks, prospect_info_button_html

from .factories import seed_actual_pick, seed_draft_slot, seed_prediction, seed_prospect


def test_on_the_clock_dedupes_candidates_across_models(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Chicago White Sox")
    player_a = seed_prospect(conn, person_id=1, person_full_name="Player A", rank=1)
    player_b = seed_prospect(conn, person_id=2, person_full_name="Player B", rank=2)

    # Player A is the top pick for both models - the old query returned one
    # row per (player, model), so this player would show up twice and crowd
    # out a genuinely distinct candidate like Player B.
    seed_prediction(
        conn, pick_number=1, team_name="Chicago White Sox", player_name="Player A",
        predicted_probability=0.7, model_version="heuristic_v1",
        mlb_person_id=player_a["mlb_person_id"],
    )
    seed_prediction(
        conn, pick_number=1, team_name="Chicago White Sox", player_name="Player A",
        predicted_probability=0.3, model_version="mock_consensus_v2",
        mlb_person_id=player_a["mlb_person_id"],
    )
    seed_prediction(
        conn, pick_number=1, team_name="Chicago White Sox", player_name="Player B",
        predicted_probability=0.2, model_version="heuristic_v1",
        mlb_person_id=player_b["mlb_person_id"],
    )

    data = fetch_dashboard_data(conn, 2026)
    candidates = data["on_the_clock"]["candidates"]
    names = [c["player_name"] for c in candidates]

    assert names.count("Player A") == 1
    assert names.count("Player B") == 1

    by_name = {c["player_name"]: c for c in candidates}
    assert by_name["Player A"]["predicted_probability"] == 0.7  # max across models
    assert by_name["Player A"]["model_count"] == 2
    assert by_name["Player B"]["model_count"] == 1


def test_on_the_clock_is_none_once_draft_is_complete(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Chicago White Sox")
    seed_actual_pick(conn, pick_number=1, team_name="Chicago White Sox", player_name="Someone")

    data = fetch_dashboard_data(conn, 2026)

    assert data["on_the_clock"] is None


def test_best_available_excludes_drafted_and_adds_mock_team(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Some Team")
    available = seed_prospect(
        conn, person_id=1, person_full_name="Available Player", rank=1,
        person_current_age=19, person_bat_side_code="L", person_pitch_hand_code="R",
    )
    seed_prospect(conn, person_id=2, person_full_name="Drafted Player", rank=2, is_drafted=True)
    seed_prediction(
        conn, pick_number=1, team_name="Some Team", player_name="Available Player",
        predicted_probability=0.6, mlb_person_id=available["mlb_person_id"],
    )

    data = fetch_dashboard_data(conn, 2026)
    names = [row["full_name"] for row in data["best_available"]]

    assert "Available Player" in names
    assert "Drafted Player" not in names

    row = next(r for r in data["best_available"] if r["full_name"] == "Available Player")
    assert row["current_age"] == 19
    assert row["bats_throws"] == "L/R"
    assert row["mock_team"] == "Some Team"
    assert row["mock_probability"] == 0.6


def test_best_available_bats_throws_blank_when_missing(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Some Team")
    seed_prospect(conn, person_id=1, person_full_name="No B/T Player", rank=1)

    data = fetch_dashboard_data(conn, 2026)
    row = next(r for r in data["best_available"] if r["full_name"] == "No B/T Player")

    assert row["bats_throws"] == ""
    assert row["mock_team"] is None


def test_draft_order_groups_by_round_in_first_pick_order(conn):
    # Pick numbers interleave rounds the way real MLB drafts do (comp picks
    # inserted between regular-round picks); the grouping should still come
    # out ordered by each round's first appearance, not by pick number.
    seed_draft_slot(conn, pick_number=1, team_name="Team A", round_label="1")
    seed_draft_slot(conn, pick_number=2, team_name="Team B", round_label="PPI")
    seed_draft_slot(conn, pick_number=3, team_name="Team C", round_label="1")

    data = fetch_dashboard_data(conn, 2026)
    labels = [g["round_label"] for g in data["draft_order"]]

    assert labels == ["1", "PPI"]
    round_1_picks = next(g for g in data["draft_order"] if g["round_label"] == "1")["picks"]
    assert [p["pick_number"] for p in round_1_picks] == [1, 3]


def test_draft_order_shows_player_name_once_drafted(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Team A", round_label="1")
    seed_draft_slot(conn, pick_number=2, team_name="Team B", round_label="1")
    seed_actual_pick(conn, pick_number=1, team_name="Team A", player_name="Drafted Player")

    data = fetch_dashboard_data(conn, 2026)
    picks = data["draft_order"][0]["picks"]
    by_pick = {p["pick_number"]: p for p in picks}

    assert by_pick[1]["player_name"] == "Drafted Player"
    assert by_pick[2]["player_name"] is None


def test_draft_order_empty_when_no_slots_loaded(conn):
    data = fetch_dashboard_data(conn, 2026)

    assert data["draft_order"] == []


def test_fetch_latest_picks_returns_ascending_order_with_summary(conn):
    seed_actual_pick(
        conn, pick_number=2, round_label="1", team_name="Team B", player_name="Second Player",
        player_position="OF", school_name="School B",
    )
    seed_actual_pick(
        conn, pick_number=1, round_label="1", team_name="Chicago White Sox", player_name="First Player",
        player_position="SS", school_name="School A",
    )

    picks = fetch_latest_picks(conn, 2026)

    assert [p["pick_number"] for p in picks] == [1, 2]
    assert picks[0]["title"] == "Round 1 · Pick 1"
    assert picks[0]["summary"] == "Chicago White Sox select First Player (SS, School A)"
    assert "team-cap-on-dark/145.svg" in picks[0]["team_logo"]
    assert picks[0]["team_logo_url"] == "https://www.mlbstatic.com/team-logos/team-cap-on-light/145.svg"


def test_fetch_latest_picks_handles_unmapped_team_name(conn):
    seed_actual_pick(conn, pick_number=1, team_name="Not A Real Team", player_name="Someone")

    picks = fetch_latest_picks(conn, 2026)

    assert picks[0]["team_logo"] == ""
    assert picks[0]["team_logo_url"] is None


def test_fetch_latest_picks_respects_limit(conn):
    for i in range(1, 6):
        seed_actual_pick(conn, pick_number=i, team_name=f"Team {i}", player_name=f"Player {i}")

    picks = fetch_latest_picks(conn, 2026, limit=3)

    # bounded to the most recent `limit` picks, but still ascending by pick number
    assert [p["pick_number"] for p in picks] == [3, 4, 5]


def test_fetch_latest_picks_empty_when_no_picks(conn):
    assert fetch_latest_picks(conn, 2026) == []


def test_fetch_latest_picks_scoped_to_draft_year(conn):
    seed_actual_pick(conn, draft_year=2025, pick_number=1, team_name="Old Team", player_name="Old Player")
    seed_actual_pick(conn, draft_year=2026, pick_number=1, team_name="New Team", player_name="New Player")

    picks = fetch_latest_picks(conn, 2026)

    assert len(picks) == 1
    assert picks[0]["team_name"] == "New Team"


def test_best_available_and_top_250_carry_blurb_and_scouting_report(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Some Team")
    seed_prospect(
        conn, person_id=1, person_full_name="Scouted Player", rank=1,
        blurb="A promising talent.", scouting_report="Plus hit tool, above-average arm.",
    )

    data = fetch_dashboard_data(conn, 2026)

    best_row = data["best_available"][0]
    assert best_row["blurb"] == "A promising talent."
    assert best_row["scouting_report"] == "Plus hit tool, above-average arm."

    board_row = data["top_250"][0]
    assert board_row["blurb"] == "A promising talent."
    assert board_row["scouting_report"] == "Plus hit tool, above-average arm."


def test_prospect_info_button_renders_only_when_text_is_present():
    with_blurb = prospect_info_button_html(
        {"full_name": "Has Blurb", "blurb": "Some text.", "scouting_report": ""}
    )
    assert "prospect-info-btn" in with_blurb
    assert 'data-name="Has Blurb"' in with_blurb
    assert 'data-blurb="Some text."' in with_blurb

    with_scouting_only = prospect_info_button_html(
        {"full_name": "Has Scouting", "blurb": "", "scouting_report": "Some report."}
    )
    assert "prospect-info-btn" in with_scouting_only

    without_either = prospect_info_button_html(
        {"full_name": "No Text", "blurb": None, "scouting_report": None}
    )
    assert without_either == ""

    missing_keys_entirely = prospect_info_button_html({"full_name": "No Keys At All"})
    assert missing_keys_entirely == ""


def test_describe_prospect_source_no_data():
    assert describe_prospect_source([]) == "No data loaded"


def test_describe_prospect_source_maps_known_values():
    assert describe_prospect_source(["mlb_stats_api_prospects"]) == "Live MLB API"
    assert describe_prospect_source(["baseballr_mlb_draft_prospects"]) == "baseballr"
    assert describe_prospect_source(["mlb_pipeline_draft_prospects_manual_csv"]) == "CSV snapshot"
    assert describe_prospect_source(["no_r_pipeline_scrape"]) == "No-R scrape"


def test_describe_prospect_source_falls_back_to_raw_value_for_unknown_source():
    assert describe_prospect_source(["some_future_source"]) == "some_future_source"


def test_describe_prospect_source_flags_mixed_state():
    result = describe_prospect_source(["mlb_stats_api_prospects", "mlb_pipeline_draft_prospects_manual_csv"])
    assert result.startswith("Mixed sources")
    assert "Live MLB API" in result
    assert "CSV snapshot" in result


def test_summary_includes_prospect_source(conn):
    seed_prospect(conn, person_id=1, person_full_name="Some Prospect", rank=1, source="mlb_stats_api_prospects")

    data = fetch_dashboard_data(conn, 2026)

    assert data["summary"]["prospect_source"] == "Live MLB API"
