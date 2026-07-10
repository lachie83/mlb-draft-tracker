from __future__ import annotations

from datetime import datetime

from mlb_tracker.draft_schedule import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    ET,
    current_milestone,
    next_milestone,
    recommended_poll_interval_seconds,
)


def test_current_milestone_none_before_draft_starts():
    assert current_milestone(datetime(2026, 7, 11, 12, 59, tzinfo=ET)) is None


def test_current_milestone_start_boundary_is_inclusive():
    m = current_milestone(datetime(2026, 7, 11, 13, 0, tzinfo=ET))
    assert m is not None
    assert m.key == "day1_preview"


def test_current_milestone_end_boundary_is_exclusive():
    # 2:30pm is when day1_early starts, not the tail end of day1_preview
    m = current_milestone(datetime(2026, 7, 11, 14, 30, tzinfo=ET))
    assert m.key == "day1_early"


def test_current_milestone_mid_window():
    m = current_milestone(datetime(2026, 7, 11, 18, 0, tzinfo=ET))
    assert m.key == "day1_late"
    assert m.picks_label == "Picks 41-135"


def test_current_milestone_none_between_day1_and_day2():
    assert current_milestone(datetime(2026, 7, 11, 23, 0, tzinfo=ET)) is None
    assert current_milestone(datetime(2026, 7, 12, 8, 0, tzinfo=ET)) is None


def test_current_milestone_day2():
    m = current_milestone(datetime(2026, 7, 12, 12, 0, tzinfo=ET))
    assert m.key == "day2"


def test_current_milestone_none_after_draft_ends():
    assert current_milestone(datetime(2026, 7, 12, 19, 30, tzinfo=ET)) is None
    assert current_milestone(datetime(2026, 7, 13, 12, 0, tzinfo=ET)) is None


def test_current_milestone_accepts_other_timezones():
    # 5:00pm UTC == 1:00pm ET (July, EDT) - should resolve to the same window
    # as if it had been passed directly in ET.
    from zoneinfo import ZoneInfo
    utc_now = datetime(2026, 7, 11, 17, 0, tzinfo=ZoneInfo("UTC"))
    assert current_milestone(utc_now).key == "day1_preview"


def test_next_milestone_returns_soonest_upcoming():
    m = next_milestone(datetime(2026, 7, 11, 12, 0, tzinfo=ET))
    assert m.key == "day1_preview"

    m = next_milestone(datetime(2026, 7, 11, 20, 0, tzinfo=ET))
    assert m.key == "day2"


def test_next_milestone_none_after_last_window():
    assert next_milestone(datetime(2026, 7, 12, 19, 30, tzinfo=ET)) is None


def test_recommended_poll_interval_matches_active_milestone():
    assert recommended_poll_interval_seconds(datetime(2026, 7, 11, 18, 0, tzinfo=ET)) == 15
    assert recommended_poll_interval_seconds(datetime(2026, 7, 12, 12, 0, tzinfo=ET)) == 10


def test_recommended_poll_interval_default_outside_any_window():
    assert recommended_poll_interval_seconds(datetime(2026, 7, 10, 12, 0, tzinfo=ET)) == DEFAULT_POLL_INTERVAL_SECONDS
