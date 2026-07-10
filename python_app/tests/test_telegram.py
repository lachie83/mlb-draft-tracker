from __future__ import annotations

import pytest
import requests

from mlb_tracker.db import get_sent_event
from mlb_tracker.draft_schedule import DRAFT_2026_MILESTONES
from mlb_tracker.telegram import (
    TelegramNotifier,
    format_pick_summary,
    format_pick_title,
    format_prospect_changes_message,
    make_milestone_message,
    make_pick_message,
    round_display_name,
    send_milestone_notification_if_new,
    send_pick_if_new,
)

from .factories import seed_prospect


def test_round_display_name_maps_known_codes():
    assert round_display_name("PPI") == "Prospect Promotion Incentive"
    assert round_display_name("CB-A") == "Competitive Balance Round A"
    assert round_display_name("CB-B") == "Competitive Balance Round B"
    assert round_display_name("SUP-2") == "Supplemental Round 2"


def test_round_display_name_formats_numeric_rounds():
    assert round_display_name("1") == "Round 1"
    assert round_display_name("20") == "Round 20"


def test_round_display_name_falls_back_for_unrecognized_codes():
    # "2C" isn't in the known-code map and isn't purely numeric, so it
    # should be shown as-is rather than guessing at a real MLB term.
    assert round_display_name("2C") == "Round 2C"
    assert round_display_name(None) == "Round"


def test_format_pick_title_combines_round_and_pick_number():
    assert format_pick_title({"round_label": "1", "pick_number": 5}) == "Round 1 · Pick 5"
    assert format_pick_title({"round_label": "PPI", "pick_number": 26}) == "Prospect Promotion Incentive · Pick 26"


def test_format_pick_summary_fills_in_defaults_for_missing_fields():
    summary = format_pick_summary({"team_name": "Seattle Mariners", "player_name": "Someone"})

    assert summary == "Seattle Mariners select Someone (N/A, Unknown School)"


def test_make_pick_message_reuses_shared_formatting(conn):
    pick_row = {
        "pick_number": 3,
        "round_label": "1",
        "team_name": "Seattle Mariners",
        "player_name": "Someone Picked",
        "player_position": "OF",
        "school_name": "Some School",
    }

    message = make_pick_message(conn, draft_year=2026, pick_row=pick_row)

    assert format_pick_title(pick_row) in message
    assert format_pick_summary(pick_row) in message


def disabled_notifier(monkeypatch) -> TelegramNotifier:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    notifier = TelegramNotifier()
    assert notifier.enabled is False
    return notifier


def test_make_pick_message_includes_pick_details_and_best_available(conn):
    seed_prospect(conn, person_id=1, person_full_name="Best Guy", rank=1)
    pick_row = {
        "pick_number": 3,
        "team_name": "Seattle Mariners",
        "player_name": "Someone Picked",
        "player_position": "OF",
        "school_name": "Some School",
    }

    message = make_pick_message(conn, draft_year=2026, pick_row=pick_row)

    assert "Seattle Mariners" in message
    assert "Someone Picked" in message
    assert "Best Guy" in message


def test_send_pick_if_new_marks_event_sent(conn, monkeypatch):
    notifier = disabled_notifier(monkeypatch)
    pick_row = {
        "pick_number": 1,
        "team_name": "Washington Nationals",
        "player_name": "Grady Emerson",
        "player_position": "SS",
        "school_name": "Fort Worth Christian (TX)",
    }

    result = send_pick_if_new(conn, notifier, draft_year=2026, pick_row=pick_row)

    assert result["reason"] == "telegram not configured"
    assert get_sent_event(conn, "draft_pick:2026:1") is not None


def test_send_pick_if_new_does_not_resend_duplicate_pick(conn, monkeypatch):
    notifier = disabled_notifier(monkeypatch)
    pick_row = {
        "pick_number": 1,
        "team_name": "Washington Nationals",
        "player_name": "Grady Emerson",
        "player_position": "SS",
        "school_name": "Fort Worth Christian (TX)",
    }

    send_pick_if_new(conn, notifier, draft_year=2026, pick_row=pick_row)

    calls = []
    notifier.send = lambda text: calls.append(text) or {"ok": True}
    second = send_pick_if_new(conn, notifier, draft_year=2026, pick_row=pick_row)

    assert second == {"ok": True, "status": "already_sent", "event_key": "draft_pick:2026:1"}
    assert calls == []


def test_send_pick_if_new_resends_when_pick_details_change(conn, monkeypatch):
    notifier = disabled_notifier(monkeypatch)
    pick_row = {
        "pick_number": 1,
        "team_name": "Washington Nationals",
        "player_name": "Grady Emerson",
        "player_position": "SS",
        "school_name": "Fort Worth Christian (TX)",
    }
    send_pick_if_new(conn, notifier, draft_year=2026, pick_row=pick_row)

    calls = []
    notifier.send = lambda text: calls.append(text) or {"ok": True}
    corrected_pick_row = {**pick_row, "player_name": "Corrected Player Name"}
    send_pick_if_new(conn, notifier, draft_year=2026, pick_row=corrected_pick_row)

    assert len(calls) == 1


def test_make_milestone_message_includes_label_picks_and_channels():
    milestone = next(m for m in DRAFT_2026_MILESTONES if m.key == "day1_late")

    message = make_milestone_message(2026, milestone)

    assert milestone.label in message
    assert milestone.picks_label in message
    assert milestone.channels in message


def test_send_milestone_notification_if_new_marks_event_sent(conn, monkeypatch):
    notifier = disabled_notifier(monkeypatch)
    milestone = next(m for m in DRAFT_2026_MILESTONES if m.key == "day1_early")

    result = send_milestone_notification_if_new(conn, notifier, draft_year=2026, milestone=milestone)

    assert result["reason"] == "telegram not configured"
    assert get_sent_event(conn, "draft_milestone:2026:day1_early") is not None


def test_send_milestone_notification_if_new_does_not_resend(conn, monkeypatch):
    notifier = disabled_notifier(monkeypatch)
    milestone = next(m for m in DRAFT_2026_MILESTONES if m.key == "day1_early")
    send_milestone_notification_if_new(conn, notifier, draft_year=2026, milestone=milestone)

    calls = []
    notifier.send = lambda text: calls.append(text) or {"ok": True}
    second = send_milestone_notification_if_new(conn, notifier, draft_year=2026, milestone=milestone)

    assert second == {"ok": True, "status": "already_sent", "event_key": "draft_milestone:2026:day1_early"}
    assert calls == []


def test_send_milestone_notification_if_new_is_independent_per_milestone(conn, monkeypatch):
    notifier = disabled_notifier(monkeypatch)
    preview = next(m for m in DRAFT_2026_MILESTONES if m.key == "day1_preview")
    early = next(m for m in DRAFT_2026_MILESTONES if m.key == "day1_early")

    send_milestone_notification_if_new(conn, notifier, draft_year=2026, milestone=preview)
    send_milestone_notification_if_new(conn, notifier, draft_year=2026, milestone=early)

    assert get_sent_event(conn, "draft_milestone:2026:day1_preview") is not None
    assert get_sent_event(conn, "draft_milestone:2026:day1_early") is not None


def test_send_raises_with_telegrams_error_description(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "fake-chat-id")
    notifier = TelegramNotifier()
    assert notifier.enabled is True

    class FakeResponse:
        status_code = 400
        text = '{"ok": false, "error_code": 400, "description": "Bad Request: chat not found"}'

        def raise_for_status(self):
            raise requests.exceptions.HTTPError("400 Client Error: Bad Request", response=self)

        def json(self):
            return {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse())

    with pytest.raises(requests.exceptions.HTTPError, match="chat not found"):
        notifier.send("hello")


def test_format_prospect_changes_message_returns_none_when_nothing_changed():
    empty_diff = {"new_entrants": [], "dropped": [], "rank_changes": []}
    assert format_prospect_changes_message(2026, empty_diff) is None


def test_format_prospect_changes_message_renders_each_section():
    diff = {
        "new_entrants": [{"mlb_person_id": 1, "rank": 45, "full_name": "New Guy"}],
        "dropped": [{"mlb_person_id": 2, "rank": 240, "full_name": "Old Guy"}],
        "rank_changes": [
            {"mlb_person_id": 3, "full_name": "Shifted Guy", "old_rank": 18, "new_rank": 12}
        ],
    }

    message = format_prospect_changes_message(2026, diff)

    assert "2026" in message
    assert "New entrants (1):" in message
    assert "#45 New Guy" in message
    assert "Dropped (1):" in message
    assert "#240 Old Guy" in message
    assert "Rank changes (1):" in message
    assert "Shifted Guy: #18 → #12" in message


def test_format_prospect_changes_message_caps_long_sections():
    new_entrants = [{"mlb_person_id": i, "rank": i, "full_name": f"Player {i}"} for i in range(1, 16)]
    diff = {"new_entrants": new_entrants, "dropped": [], "rank_changes": []}

    message = format_prospect_changes_message(2026, diff, max_items=10)

    assert "New entrants (15):" in message
    assert "...and 5 more" in message
    assert "Player 11" not in message
