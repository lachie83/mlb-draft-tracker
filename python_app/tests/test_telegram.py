from __future__ import annotations

from mlb_tracker.db import get_sent_event
from mlb_tracker.telegram import TelegramNotifier, make_pick_message, send_pick_if_new

from .factories import seed_prospect


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
