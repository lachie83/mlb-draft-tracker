from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import requests

from .db import get_best_available, get_sent_event, mark_event_sent


class TelegramNotifier:
    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, text: str) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "reason": "telegram not configured", "text": text}
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        resp = requests.post(url, json={"chat_id": self.chat_id, "text": text}, timeout=30)
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            try:
                detail = resp.json().get("description", resp.text)
            except ValueError:
                detail = resp.text
            raise requests.exceptions.HTTPError(f"{exc} - Telegram says: {detail}", response=resp) from exc
        return resp.json()


# Shared with the dashboard (which imports this rather than keeping its own
# copy) so a round code is described identically everywhere - the round-tabs
# UI, the in-browser toast, and the Telegram message.
ROUND_LABEL_NAMES = {
    "PPI": "Prospect Promotion Incentive",
    "CB-A": "Competitive Balance Round A",
    "CB-B": "Competitive Balance Round B",
    "SUP-2": "Supplemental Round 2",
}


def round_display_name(round_label):
    if round_label in ROUND_LABEL_NAMES:
        return ROUND_LABEL_NAMES[round_label]
    if round_label and str(round_label).isdigit():
        return f"Round {round_label}"
    return f"Round {round_label}" if round_label else "Round"


def format_pick_title(pick_row: dict[str, Any]) -> str:
    """"Round 1 · Pick 5" style header, shared by the Telegram message and
    the dashboard's in-browser pick notifications."""
    return f"{round_display_name(pick_row.get('round_label'))} · Pick {pick_row['pick_number']}"


def format_pick_summary(pick_row: dict[str, Any]) -> str:
    """The one-line "who got picked" summary, shared by the Telegram message
    and the dashboard's in-browser pick notifications so both channels
    describe a pick with identical wording."""
    position = pick_row.get("player_position") or "N/A"
    school = pick_row.get("school_name") or "Unknown School"
    return f"{pick_row['team_name']} select {pick_row['player_name']} ({position}, {school})"


def make_pick_message(conn, draft_year: int, pick_row: dict[str, Any]) -> str:
    best = get_best_available(conn, draft_year, limit=3)
    remaining = ", ".join(f"#{row['rank']} {row['full_name']}" for row in best)
    board_rank = "?"
    if pick_row.get("prospect_id"):
        r = conn.execute("SELECT rank FROM prospects WHERE prospect_id = ?", (pick_row["prospect_id"],)).fetchone()
        if r and r[0] is not None:
            board_rank = r[0]
    return (
        f"MLB Draft {draft_year} — {format_pick_title(pick_row)}\n"
        f"{format_pick_summary(pick_row)}\n"
        f"Board rank: #{board_rank}\n"
        f"Best available: {remaining}"
    )


def send_pick_if_new(conn, notifier: TelegramNotifier, draft_year: int, pick_row: dict[str, Any]) -> dict[str, Any]:
    event_key = f"draft_pick:{draft_year}:{pick_row['pick_number']}"
    message = make_pick_message(conn, draft_year, pick_row)
    payload_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
    existing = get_sent_event(conn, event_key)
    if existing and existing["payload_hash"] == payload_hash:
        return {"ok": True, "status": "already_sent", "event_key": event_key}
    result = notifier.send(message)
    if result.get("ok", True) or result.get("reason") == "telegram not configured":
        mark_event_sent(conn, event_key, payload_hash, pick_row["pick_number"], message)
    return result
