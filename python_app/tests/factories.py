from __future__ import annotations

from typing import Any

from mlb_tracker.db import upsert_draft_slot, upsert_prospect
from mlb_tracker.sources import normalize_prospect_row


def make_raw_prospect(**overrides: Any) -> dict[str, Any]:
    raw = {
        "person_id": 100001,
        "person_full_name": "Test Prospect",
        "person_first_name": "Test",
        "person_last_name": "Prospect",
        "rank": 1,
        "person_primary_position_name": "Shortstop",
        "person_primary_position_abbreviation": "SS",
        "school_name": "Test University",
        "school_school_class": "4YR JR",
        "is_drafted": False,
        "is_pass": False,
        "pick_round": None,
        "pick_number": None,
        "team_id": None,
        "team_name": None,
        "team_abbreviation": None,
        "person_active": True,
    }
    raw.update(overrides)
    return raw


def seed_prospect(conn, draft_year: int = 2026, **overrides: Any) -> dict[str, Any]:
    raw = make_raw_prospect(**overrides)
    normalized = normalize_prospect_row(raw, draft_year)
    upsert_prospect(conn, normalized)
    conn.commit()
    return normalized


def seed_draft_slot(conn, draft_year: int = 2026, pick_number: int = 1, team_name: str = "Test Team", **overrides: Any) -> dict[str, Any]:
    slot = {
        "draft_year": draft_year,
        "round_label": overrides.pop("round_label", "Round 1"),
        "round_pick_number": overrides.pop("round_pick_number", pick_number),
        "pick_number": pick_number,
        "team_id": overrides.pop("team_id", None),
        "team_name": team_name,
        "team_abbrev": overrides.pop("team_abbrev", None),
        "slot_type": overrides.pop("slot_type", "first_round"),
        "pick_value": overrides.pop("pick_value", None),
        "bonus_pool_value": overrides.pop("bonus_pool_value", None),
        "compensation_for": overrides.pop("compensation_for", None),
        "acquired_from": overrides.pop("acquired_from", None),
        "notes": overrides.pop("notes", None),
        "source": overrides.pop("source", "test_seed"),
        "source_url": overrides.pop("source_url", None),
        "raw_payload": overrides.pop("raw_payload", None),
    }
    upsert_draft_slot(conn, slot)
    conn.commit()
    return slot
