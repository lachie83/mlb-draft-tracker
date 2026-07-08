from __future__ import annotations

from typing import Any

from mlb_tracker.db import (
    upsert_actual_pick,
    upsert_draft_slot,
    upsert_mock_draft_pick,
    upsert_prospect,
)
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
    # normalize_prospect_row() always hardcodes source to the baseballr
    # value regardless of what's in the raw dict (every real sync path
    # explicitly overrides it afterward - see mlb_stats_api.py/main.py) -
    # do the same here so tests can actually control it.
    source = overrides.pop("source", None)
    raw = make_raw_prospect(**overrides)
    normalized = normalize_prospect_row(raw, draft_year)
    if source is not None:
        normalized["source"] = source
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


def seed_mock_draft_pick(
    conn,
    draft_year: int = 2026,
    pick_number: int = 1,
    team_name: str = "Test Team",
    player_name: str = "Test Prospect",
    source_name: str = "Test Mock Source",
    source_date: str = "2026-06-01",
    weight: float = 1.0,
    **overrides: Any,
) -> dict[str, Any]:
    row = {
        "draft_year": draft_year,
        "source_name": source_name,
        "source_authors": overrides.pop("source_authors", None),
        "source_date": source_date,
        "source_url": overrides.pop("source_url", None),
        "weight": weight,
        "pick_number": pick_number,
        "team_name": team_name,
        "player_name": player_name,
        "prospect_id": overrides.pop("prospect_id", None),
        "mlb_person_id": overrides.pop("mlb_person_id", None),
        "board_rank": overrides.pop("board_rank", None),
        "notes": overrides.pop("notes", None),
    }
    upsert_mock_draft_pick(conn, row)
    conn.commit()
    return row


def seed_prediction(
    conn,
    draft_year: int = 2026,
    pick_number: int = 1,
    team_name: str = "Test Team",
    player_name: str = "Test Prospect",
    predicted_probability: float = 0.5,
    model_version: str = "heuristic_v1",
    mlb_person_id: int | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    sql = """
    INSERT INTO predictions (
        draft_year, pick_number, team_name, prospect_id, mlb_person_id, player_name,
        predicted_probability, rank_score, mock_score, fit_score, buzz_score,
        model_version, prediction_source, notes, raw_payload
    ) VALUES (
        :draft_year, :pick_number, :team_name, :prospect_id, :mlb_person_id, :player_name,
        :predicted_probability, :rank_score, :mock_score, :fit_score, :buzz_score,
        :model_version, :prediction_source, :notes, :raw_payload
    )
    """
    row = {
        "draft_year": draft_year,
        "pick_number": pick_number,
        "team_name": team_name,
        "prospect_id": overrides.pop("prospect_id", None),
        "mlb_person_id": mlb_person_id,
        "player_name": player_name,
        "predicted_probability": predicted_probability,
        "rank_score": overrides.pop("rank_score", None),
        "mock_score": overrides.pop("mock_score", None),
        "fit_score": overrides.pop("fit_score", None),
        "buzz_score": overrides.pop("buzz_score", None),
        "model_version": model_version,
        "prediction_source": overrides.pop("prediction_source", "test_seed"),
        "notes": overrides.pop("notes", None),
        "raw_payload": overrides.pop("raw_payload", None),
    }
    conn.execute(sql, row)
    conn.commit()
    return row


def seed_actual_pick(
    conn,
    draft_year: int = 2026,
    pick_number: int = 1,
    team_name: str = "Test Team",
    player_name: str = "Test Prospect",
    **overrides: Any,
) -> dict[str, Any]:
    row = {
        "draft_year": draft_year,
        "pick_number": pick_number,
        "round_label": overrides.pop("round_label", "Round 1"),
        "round_pick_number": overrides.pop("round_pick_number", pick_number),
        "team_id": overrides.pop("team_id", None),
        "team_name": team_name,
        "team_abbrev": overrides.pop("team_abbrev", None),
        "prospect_id": overrides.pop("prospect_id", None),
        "mlb_person_id": overrides.pop("mlb_person_id", None),
        "player_name": player_name,
        "player_position": overrides.pop("player_position", None),
        "school_name": overrides.pop("school_name", None),
        "source": overrides.pop("source", "test_seed"),
        "source_event_id": overrides.pop("source_event_id", None),
        "picked_at": overrides.pop("picked_at", None),
        "signed_status": overrides.pop("signed_status", None),
        "bonus_amount": overrides.pop("bonus_amount", None),
        "slot_value": overrides.pop("slot_value", None),
        "raw_payload": overrides.pop("raw_payload", None),
    }
    upsert_actual_pick(conn, row)
    conn.commit()
    return row
