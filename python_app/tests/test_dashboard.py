from __future__ import annotations

from dashboard import (
    class_label,
    commitment_tier_for_school,
    compute_signability,
    describe_prospect_source,
    extract_commitment_school,
    fetch_dashboard_data,
    fetch_latest_picks,
    fetch_team_pool_data,
    format_usd,
    get_selected_theme,
    prior_draft_return,
    prospect_info_button_html,
    signability_badge_html,
    signability_tag,
    signability_tag_html,
)

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


def test_draft_order_carries_pick_value(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Team A", round_label="1", pick_value=11_350_600)

    data = fetch_dashboard_data(conn, 2026)
    pick = data["draft_order"][0]["picks"][0]

    assert pick["pick_value"] == 11_350_600


def test_on_the_clock_carries_pick_value(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Team A", round_label="1", pick_value=11_350_600)

    data = fetch_dashboard_data(conn, 2026)

    assert data["on_the_clock"]["pick_value"] == 11_350_600


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


def test_get_selected_theme_none_when_no_cookie():
    assert get_selected_theme({}) is None


def test_get_selected_theme_reads_valid_cookie_values():
    assert get_selected_theme({"HTTP_COOKIE": "mlb_theme=light"}) == "light"
    assert get_selected_theme({"HTTP_COOKIE": "mlb_theme=dark"}) == "dark"


def test_get_selected_theme_ignores_unknown_value():
    assert get_selected_theme({"HTTP_COOKIE": "mlb_theme=purple"}) is None


def test_get_selected_theme_reads_alongside_other_cookies():
    assert get_selected_theme({"HTTP_COOKIE": "other=1; mlb_theme=light; another=2"}) == "light"


def test_get_selected_theme_none_on_malformed_cookie_header():
    assert get_selected_theme({"HTTP_COOKIE": "not a valid \x00 cookie header"}) is None


def test_class_label_maps_known_school_class_values():
    assert class_label("HS SR") == "High School"
    assert class_label("JC J1") == "JUCO"
    assert class_label("JC J2") == "JUCO"
    assert class_label("4YR FR") == "College Fr"
    assert class_label("4YR SO") == "College So"
    assert class_label("4YR JR") == "College Jr"
    assert class_label("4YR SR") == "College Sr"
    assert class_label("4YR 5S") == "College 5th Yr"
    assert class_label("4YR GR") == "Grad Student"


def test_class_label_handles_missing_or_unknown():
    assert class_label(None) == "Unknown"
    assert class_label("") == "Unknown"
    assert class_label("NS") == "Unknown"
    assert class_label("something odd") == "College"


def test_signability_tag_steal_when_pick_well_after_rank():
    # Ranked #5, drafted at pick #40 - fell 35 spots past consensus, a steal.
    tag, delta = signability_tag(rank=5, pick_number=40)
    assert tag == "Steal"
    assert delta == 35


def test_signability_tag_reach_when_pick_well_before_rank():
    # Ranked #40, drafted at pick #5 - team reached 35 spots ahead of consensus.
    tag, delta = signability_tag(rank=40, pick_number=5)
    assert tag == "Reach"
    assert delta == -35


def test_signability_tag_none_within_threshold():
    tag, delta = signability_tag(rank=10, pick_number=15)
    assert tag is None
    assert delta == 5


def test_signability_tag_none_at_exact_threshold_boundary():
    # threshold=20 is exclusive (> / <), not inclusive
    tag, delta = signability_tag(rank=10, pick_number=30)
    assert tag is None
    assert delta == 20


def test_signability_tag_none_when_rank_unknown():
    assert signability_tag(rank=None, pick_number=10) == (None, None)


def test_signability_tag_respects_custom_threshold():
    tag, delta = signability_tag(rank=10, pick_number=15, threshold=3)
    assert tag == "Steal"
    assert delta == 5


def test_signability_tag_html_renders_steal_and_reach_badges():
    steal_html = signability_tag_html({"signability_tag": "Steal", "signability_delta": 35})
    assert "Steal (+35)" in steal_html
    assert "badge-accent" in steal_html

    reach_html = signability_tag_html({"signability_tag": "Reach", "signability_delta": -35})
    assert "Reach (-35)" in reach_html
    assert "badge-warning" in reach_html


def test_signability_tag_html_empty_when_no_tag():
    assert signability_tag_html({"signability_tag": None, "signability_delta": -5}) == ""
    assert signability_tag_html({}) == ""


def test_format_usd():
    assert format_usd(None) == ""
    assert format_usd(1350600) == "$1,350,600"
    assert format_usd(0) == "$0"


def test_fetch_team_pool_data_computes_totals_used_and_remaining(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Team A", pick_value=10_000_000)
    seed_draft_slot(conn, pick_number=31, team_name="Team A", pick_value=2_000_000)
    seed_draft_slot(conn, pick_number=2, team_name="Team B", pick_value=9_000_000)
    # Round 11+ picks report pick_value=0 in the real API and don't count
    # toward the bonus pool - excluded by the WHERE pick_value > 0 clause.
    seed_draft_slot(conn, pick_number=400, team_name="Team A", pick_value=0)

    seed_actual_pick(conn, pick_number=1, team_name="Team A", bonus_amount=8_500_000, slot_value=10_000_000)

    rows = fetch_team_pool_data(conn, 2026)
    by_team = {r["team_name"]: r for r in rows}

    assert by_team["Team A"]["pool_total"] == 12_000_000
    assert by_team["Team A"]["pool_used"] == 8_500_000
    assert by_team["Team A"]["pool_remaining"] == 3_500_000
    assert abs(by_team["Team A"]["pct_used"] - (8_500_000 / 12_000_000)) < 1e-9

    assert by_team["Team B"]["pool_total"] == 9_000_000
    assert by_team["Team B"]["pool_used"] == 0
    assert by_team["Team B"]["pool_remaining"] == 9_000_000


def test_fetch_team_pool_data_sorted_by_remaining_ascending(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Committed Team", pick_value=10_000_000)
    seed_actual_pick(conn, pick_number=1, team_name="Committed Team", bonus_amount=9_500_000, slot_value=10_000_000)
    seed_draft_slot(conn, pick_number=2, team_name="Fresh Team", pick_value=10_000_000)

    rows = fetch_team_pool_data(conn, 2026)

    assert [r["team_name"] for r in rows] == ["Committed Team", "Fresh Team"]


def test_fetch_team_pool_data_empty_when_no_draft_slots(conn):
    assert fetch_team_pool_data(conn, 2026) == []


def test_fetch_team_pool_data_round_1_10_pick_counts_full_bonus_against_pool(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Team A", pick_value=10_000_000)
    # a round 1-10 signing (slot_value > 0) counts in full, even though it's
    # well under the $150k post-10th-round threshold - that threshold only
    # applies to picks with no assigned slot value.
    seed_actual_pick(conn, pick_number=1, team_name="Team A", bonus_amount=50_000, slot_value=10_000_000)

    rows = fetch_team_pool_data(conn, 2026)

    assert rows[0]["pool_used"] == 50_000


def test_fetch_team_pool_data_round_11_plus_pick_under_threshold_does_not_count(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Team A", pick_value=10_000_000)
    # a round 11+ pick has no assigned slot_value in the real API; bonuses
    # up to $150k for those picks don't count against the pool at all.
    seed_actual_pick(conn, pick_number=400, team_name="Team A", bonus_amount=100_000, slot_value=None)

    rows = fetch_team_pool_data(conn, 2026)

    assert rows[0]["pool_used"] == 0


def test_fetch_team_pool_data_round_11_plus_pick_over_threshold_counts_the_excess(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Team A", pick_value=10_000_000)
    seed_actual_pick(conn, pick_number=400, team_name="Team A", bonus_amount=200_000, slot_value=None)

    rows = fetch_team_pool_data(conn, 2026)

    assert rows[0]["pool_used"] == 50_000


def test_fetch_team_pool_data_unsigned_pick_does_not_count(conn):
    seed_draft_slot(conn, pick_number=1, team_name="Team A", pick_value=10_000_000)
    seed_actual_pick(conn, pick_number=400, team_name="Team A", bonus_amount=None, slot_value=None)

    rows = fetch_team_pool_data(conn, 2026)

    assert rows[0]["pool_used"] == 0


def test_picks_query_carries_rank_signed_status_and_signability_tag(conn):
    prospect = seed_prospect(conn, person_id=777, person_full_name="Fallen Star", rank=5)
    seed_actual_pick(
        conn, pick_number=40, team_name="Some Team", player_name="Fallen Star",
        mlb_person_id=prospect["mlb_person_id"], bonus_amount=3_000_000, slot_value=2_500_000,
    )

    data = fetch_dashboard_data(conn, 2026)
    pick = next(p for p in data["picks"] if p["pick_number"] == 40)

    assert pick["rank"] == 5
    assert pick["signed_status"] == "Signed"
    assert pick["signability_tag"] == "Steal"
    assert pick["signability_delta"] == 35


def test_picks_query_unsigned_status_when_no_bonus_reported(conn):
    seed_actual_pick(conn, pick_number=1, team_name="Some Team", player_name="Someone")

    data = fetch_dashboard_data(conn, 2026)
    pick = next(p for p in data["picks"] if p["pick_number"] == 1)

    assert pick["signed_status"] == "Unsigned"
    assert pick["signability_tag"] is None  # no matching prospect -> rank unknown


def test_prospect_info_button_renders_for_position_and_school_without_blurb():
    # Actual Picks rows often won't have a written blurb, but position/
    # school alone should still be enough to show the info button.
    html = prospect_info_button_html(
        {"full_name": "Some Player", "position_name": "Shortstop", "school_name": "Test U"}
    )
    assert "prospect-info-btn" in html
    assert 'data-position="Shortstop"' in html
    assert 'data-school="Test U"' in html


def test_prospect_info_button_carries_enrichment_attributes():
    html = prospect_info_button_html(
        {
            "full_name": "Some Player",
            "blurb": "text",
            "bats": "L",
            "throws": "R",
            "home_city": "Austin",
            "home_state": "TX",
            "school_class": "4YR JR",
            "headshot_link": "https://example.com/photo.png",
            "bonus_amount": 3_000_000,
            "slot_value": 2_500_000,
        }
    )
    assert 'data-bt="L/R"' in html
    assert 'data-hometown="Austin, TX"' in html
    assert 'data-class="College Jr"' in html
    assert 'data-headshot="https://example.com/photo.png"' in html
    assert 'data-bonus="$3,000,000"' in html
    assert 'data-slot="$2,500,000"' in html
    assert 'data-over-under="+$500,000"' in html


def test_extract_commitment_school_high_confidence_phrasing():
    assert extract_commitment_school("Both he and Spangler are committed to Stanford for college ball") == "Stanford"
    assert extract_commitment_school("If he follows through on his commitment to Alabama") == "Alabama"
    assert extract_commitment_school("committed to Louisiana State this fall") == "Louisiana State"


def test_extract_commitment_school_none_when_no_high_confidence_phrasing():
    assert extract_commitment_school(None) is None
    assert extract_commitment_school("") is None
    assert extract_commitment_school("The Texas recruit is a fine prospect") is None
    assert extract_commitment_school("All of the LSU recruit's tools stand out") is None


def test_extract_commitment_school_rejects_garbage_sentence_bleed():
    # This is a real example from testing against the live board - a looser
    # pattern matched across a sentence boundary onto an unrelated phrase.
    # The strict "committed to X" / "commitment to X" patterns should not.
    blurb = "Comeau has limited defensive options. The Texas A&M recruit projects best in a corner."
    assert extract_commitment_school(blurb) is None


def test_commitment_tier_for_school_known_and_unknown():
    assert commitment_tier_for_school("Vanderbilt") == "hard"
    assert commitment_tier_for_school("Kentucky") == "medium"
    assert commitment_tier_for_school("Some Small College") == "medium"
    assert commitment_tier_for_school("Pearl River Community College") == "easy"
    assert commitment_tier_for_school(None) is None


def test_prior_draft_return_true_when_earlier_year_pick_exists(conn):
    seed_actual_pick(conn, draft_year=2025, pick_number=425, player_name="Returned Player", mlb_person_id=999)

    assert prior_draft_return(conn, 999, 2026) is True


def test_prior_draft_return_false_when_no_earlier_pick(conn):
    assert prior_draft_return(conn, 999, 2026) is False


def test_prior_draft_return_false_when_person_id_none(conn):
    assert prior_draft_return(conn, None, 2026) is False


def test_prior_draft_return_ignores_same_or_later_year(conn):
    seed_actual_pick(conn, draft_year=2026, pick_number=1, player_name="This Year", mlb_person_id=888)

    assert prior_draft_return(conn, 888, 2026) is False


def test_compute_signability_college_senior_signed_at_slot_is_likely_sign():
    result = compute_signability(
        rank=50, pick_number=52, class_label_value="College Sr",
        commitment_tier=None, pool_pct_used=0.5,
    )
    assert result["tier"] == "Likely Sign"
    assert result["score"] > 70


def test_compute_signability_hs_steal_with_hard_commitment_is_tougher():
    likely = compute_signability(
        rank=50, pick_number=52, class_label_value="College Sr",
        commitment_tier=None, pool_pct_used=0.5,
    )
    tough = compute_signability(
        rank=5, pick_number=40, class_label_value="High School",
        commitment_tier="hard", pool_pct_used=0.5,
    )
    assert tough["score"] < likely["score"]
    assert tough["tier"] in ("Moderate Risk", "Tough Sign")


def test_compute_signability_juco_scores_like_high_school_not_like_senior():
    # Regression: the source plan scored JUCO like a college senior
    # (near-auto-sign) - backwards, since a draft-eligible JUCO player can
    # return to school easily and has real leverage.
    juco = compute_signability(
        rank=None, pick_number=10, class_label_value="JUCO",
        commitment_tier=None, pool_pct_used=None,
    )
    senior = compute_signability(
        rank=None, pick_number=10, class_label_value="College Sr",
        commitment_tier=None, pool_pct_used=None,
    )
    hs = compute_signability(
        rank=None, pick_number=10, class_label_value="High School",
        commitment_tier=None, pool_pct_used=None,
    )
    assert juco["score"] == hs["score"]
    assert juco["score"] < senior["score"]


def test_compute_signability_prior_return_lowers_score():
    without = compute_signability(
        rank=None, pick_number=10, class_label_value="College Jr",
        commitment_tier=None, pool_pct_used=None, prior_return=False,
    )
    with_return = compute_signability(
        rank=None, pick_number=10, class_label_value="College Jr",
        commitment_tier=None, pool_pct_used=None, prior_return=True,
    )
    assert with_return["score"] < without["score"]
    assert "previously drafted and didn't sign" in with_return["factors"]


def test_compute_signability_reported_bonus_tier_adjusts_score():
    over_slot = compute_signability(
        rank=None, pick_number=10, class_label_value="College Jr",
        commitment_tier=None, pool_pct_used=None, reported_bonus_tier="over_slot",
    )
    under_slot = compute_signability(
        rank=None, pick_number=10, class_label_value="College Jr",
        commitment_tier=None, pool_pct_used=None, reported_bonus_tier="under_slot",
    )
    assert under_slot["score"] > over_slot["score"]


def test_compute_signability_pool_room_factors():
    tight_pool = compute_signability(
        rank=None, pick_number=10, class_label_value="College Jr",
        commitment_tier=None, pool_pct_used=0.99,
    )
    ample_pool = compute_signability(
        rank=None, pick_number=10, class_label_value="College Jr",
        commitment_tier=None, pool_pct_used=0.2,
    )
    assert ample_pool["score"] > tight_pool["score"]


def test_compute_signability_score_clamped_to_0_100():
    worst = compute_signability(
        rank=5, pick_number=100, class_label_value="High School",
        commitment_tier="hard", pool_pct_used=0.99, prior_return=True, reported_bonus_tier="over_slot",
    )
    best = compute_signability(
        rank=100, pick_number=5, class_label_value="College Sr",
        commitment_tier="easy", pool_pct_used=0.1, reported_bonus_tier="under_slot",
    )
    assert 0 <= worst["score"] <= 100
    assert 0 <= best["score"] <= 100


def test_signability_badge_html_maps_tiers_to_badge_classes():
    likely = signability_badge_html({"signability": {"tier": "Likely Sign", "score": 85, "factors": []}})
    assert "badge-accent" in likely
    assert "Likely Sign (85)" in likely

    tough = signability_badge_html({"signability": {"tier": "Tough Sign", "score": 20, "factors": ["fell 30 spots"]}})
    assert "badge-warning" in tough


def test_signability_badge_html_empty_when_no_signability():
    assert signability_badge_html({}) == ""


def test_picks_query_includes_signability_end_to_end(conn):
    prospect = seed_prospect(conn, person_id=555, person_full_name="Test Signer", rank=5, school_class="4YR SR")
    seed_actual_pick(
        conn, pick_number=6, team_name="Some Team", player_name="Test Signer",
        mlb_person_id=prospect["mlb_person_id"], bonus_amount=100_000, slot_value=200_000,
    )

    data = fetch_dashboard_data(conn, 2026)
    pick = next(p for p in data["picks"] if p["pick_number"] == 6)

    assert pick["signability"] is not None
    assert pick["signability"]["tier"] in ("Likely Sign", "Moderate Risk", "Tough Sign")
    assert isinstance(pick["signability"]["score"], int)


def test_picks_query_ignores_commitment_mentions_for_non_hs_players(conn):
    # Regression: found via real-data testing that a college player's blurb
    # can mention their *original* HS commitment as backstory (e.g. "His
    # commitment to UCLA priced him out" for a player who's since actually
    # enrolled there and become a junior) - that's history, not a live
    # decision, and must not be treated as current signability leverage.
    prospect = seed_prospect(
        conn, person_id=555, person_full_name="College Junior", rank=2, school_class="4YR JR",
        blurb="His commitment to Vanderbilt priced him out of the prior draft.",
    )
    seed_actual_pick(
        conn, pick_number=3, team_name="Some Team", player_name="College Junior",
        mlb_person_id=prospect["mlb_person_id"],
    )

    data = fetch_dashboard_data(conn, 2026)
    pick = next(p for p in data["picks"] if p["pick_number"] == 3)

    assert "college commitment" not in " ".join(pick["signability"]["factors"])
