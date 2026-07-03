from __future__ import annotations

import json
import sqlite3
from typing import Any

from rapidfuzz import fuzz

from .db import row_to_dict, upsert_actual_pick, upsert_prospect
from .sources import fetch_baseballr_prospects_csv, normalize_prospect_row
from .telegram import TelegramNotifier, send_pick_if_new


def find_matching_prospect(conn: sqlite3.Connection, player_name: str, school_name: str | None, draft_year: int):
    rows = conn.execute(
        "SELECT * FROM prospects WHERE draft_year = ? ORDER BY rank",
        (draft_year,),
    ).fetchall()
    best = None
    best_score = -1
    for row in rows:
        score = fuzz.token_sort_ratio(player_name, row["full_name"])
        if school_name and row["school_name"]:
            score += fuzz.token_sort_ratio(school_name, row["school_name"]) * 0.15
        if score > best_score:
            best = row
            best_score = score
    return best if best_score >= 80 else None


def reconcile_live_picks(conn: sqlite3.Connection, draft_year: int = 2026, notifier: TelegramNotifier | None = None) -> list[dict[str, Any]]:
    notifier = notifier or TelegramNotifier()
    rows = fetch_baseballr_prospects_csv(draft_year)
    new_picks: list[dict[str, Any]] = []

    for raw in rows:
        norm = normalize_prospect_row(raw, draft_year)
        upsert_prospect(conn, norm)
        if not norm["is_drafted"] or norm.get("pick_number") is None:
            continue
        existing = conn.execute(
            "SELECT * FROM actual_picks WHERE draft_year = ? AND pick_number = ?",
            (draft_year, norm["pick_number"]),
        ).fetchone()
        prospect_row = conn.execute(
            "SELECT prospect_id, position_name, school_name FROM prospects WHERE mlb_person_id = ? AND draft_year = ?",
            (norm["mlb_person_id"], draft_year),
        ).fetchone()
        if prospect_row is None:
            prospect_row = find_matching_prospect(conn, norm["full_name"], norm.get("school_name"), draft_year)
        pick_row = {
            "draft_year": draft_year,
            "pick_number": norm["pick_number"],
            "round_label": norm.get("pick_round"),
            "round_pick_number": None,
            "team_id": norm.get("draft_team_id"),
            "team_name": norm.get("draft_team_name") or "Unknown Team",
            "team_abbrev": norm.get("draft_team_abbreviation"),
            "prospect_id": prospect_row["prospect_id"] if prospect_row else None,
            "mlb_person_id": norm.get("mlb_person_id"),
            "player_name": norm["full_name"],
            "player_position": prospect_row["position_name"] if prospect_row else norm.get("position_name"),
            "school_name": prospect_row["school_name"] if prospect_row else norm.get("school_name"),
            "source": "baseballr_mlb_draft_prospects",
            "source_event_id": f"{draft_year}:{norm['pick_number']}:{norm.get('mlb_person_id')}",
            "picked_at": None,
            "signed_status": None,
            "bonus_amount": None,
            "slot_value": None,
            "raw_payload": json.dumps(raw, ensure_ascii=False, sort_keys=True),
        }
        upsert_actual_pick(conn, pick_row)
        if existing is None:
            new_picks.append(pick_row)
            send_pick_if_new(conn, notifier, draft_year, pick_row)
    return new_picks
