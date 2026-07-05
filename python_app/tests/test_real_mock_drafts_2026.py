from __future__ import annotations

from mlb_tracker.real_mock_drafts_2026 import TEAM_NICKNAME_TO_FULL_NAME, full_team_name, load_real_mock_draft_picks


def test_full_team_name_maps_known_nicknames():
    assert full_team_name("White Sox") == "Chicago White Sox"
    assert full_team_name("Diamondbacks") == "Arizona Diamondbacks"
    assert full_team_name("D-backs") == "Arizona Diamondbacks"


def test_full_team_name_passes_through_unknown_values():
    assert full_team_name("Some Unmapped Team") == "Some Unmapped Team"


def test_team_nickname_map_has_no_duplicate_targets_missing():
    # sanity check the mapping table itself isn't accidentally empty/truncated
    assert len(TEAM_NICKNAME_TO_FULL_NAME) >= 30


def test_load_real_mock_draft_picks_every_row_has_required_fields():
    rows = load_real_mock_draft_picks(draft_year=2026)
    assert len(rows) > 0

    for row in rows:
        assert row["draft_year"] == 2026
        assert row["source_name"]
        assert row["source_date"]
        assert row["source_url"]
        assert row["weight"] > 0
        assert row["pick_number"] >= 1
        assert row["team_name"]
        # every team_name should already be normalized to a full team name
        assert row["team_name"] in set(TEAM_NICKNAME_TO_FULL_NAME.values())
        assert row["player_name"]


def test_load_real_mock_draft_picks_pick_one_split_sums_to_one_source_weight():
    rows = load_real_mock_draft_picks(draft_year=2026)
    july_2_pick_1 = [
        r for r in rows if r["pick_number"] == 1 and r["source_date"] == "2026-07-02"
    ]
    assert len(july_2_pick_1) == 3
    total = sum(r["weight"] for r in july_2_pick_1)
    base_weight = next(r["weight"] for r in rows if r["source_date"] == "2026-07-02" and r["pick_number"] == 2)
    assert abs(total - base_weight) < 1e-9


def test_load_real_mock_draft_picks_covers_at_least_two_distinct_sources():
    rows = load_real_mock_draft_picks(draft_year=2026)
    source_dates = {r["source_date"] for r in rows}
    assert len(source_dates) >= 2


def test_load_real_mock_draft_picks_no_exact_duplicate_source_pick_player():
    rows = load_real_mock_draft_picks(draft_year=2026)
    keys = [(r["source_name"], r["source_date"], r["pick_number"], r["player_name"]) for r in rows]
    assert len(keys) == len(set(keys))
