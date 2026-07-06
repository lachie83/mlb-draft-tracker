"""Official MLB Stats API draft data source (statsapi.mlb.com).

Free, unauthenticated, JSON. This is the same source that powers
mlb.com/draft/tracker and (per public reporting) underlies baseballr's
`mlb_draft_prospects()` R function - the pick object shape below matches
baseballr's output almost field-for-field. Non-commercial use, subject to
http://gdx.mlb.com/components/copyright.txt

Endpoints used:
- GET /draft/{year}         full draft: every round's picks, drafted or not
- GET /draft/{year}/latest  small payload: `nextUp` (who's on the clock).
  Verified pre-draft (no picks made yet) on 2026-07-05/06; the exact shape
  of "most recently made pick" info here hasn't been observed against a
  live draft yet (none was in progress), so `get_on_the_clock` only relies
  on `nextUp`, not any assumed "just picked" field. Re-verify once a draft
  is actually live before depending on more of this response.

Design note: this module only *reads* the live API and maps its objects
into the same shapes the rest of the app already understands
(normalize_prospect_row's raw-dict schema, draft_slots/actual_picks rows),
rather than introducing a parallel data model.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from .clients import HttpClient
from .db import upsert_actual_pick, upsert_draft_slot, upsert_prospect
from .sources import normalize_prospect_row
from .telegram import TelegramNotifier, send_pick_if_new

DRAFT_URL_TMPL = "https://statsapi.mlb.com/api/v1/draft/{year}"
LATEST_URL_TMPL = "https://statsapi.mlb.com/api/v1/draft/{year}/latest"
STATS_API_SOURCE = "mlb_stats_api"


def fetch_draft(year: int, client: HttpClient | None = None) -> dict[str, Any]:
    client = client or HttpClient()
    return client.get_json(DRAFT_URL_TMPL.format(year=year))


def fetch_latest(year: int, client: HttpClient | None = None) -> dict[str, Any]:
    client = client or HttpClient()
    return client.get_json(LATEST_URL_TMPL.format(year=year))


def iter_picks(draft_payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    rounds = (draft_payload.get("drafts") or {}).get("rounds") or []
    for round_ in rounds:
        yield from round_.get("picks") or []


def get_on_the_clock(latest_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The `nextUp` list from /draft/{year}/latest: upcoming picks in order,
    the first of which is on the clock right now."""
    return latest_payload.get("nextUp") or []


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pick_to_draft_slot(pick: dict[str, Any], draft_year: int) -> dict[str, Any]:
    team = pick.get("team") or {}
    round_label = pick.get("pickRound") or ""
    return {
        "draft_year": draft_year,
        "round_label": round_label,
        "round_pick_number": pick.get("roundPickNumber"),
        "pick_number": pick["pickNumber"],
        "team_id": team.get("id"),
        "team_name": team.get("name") or "Unknown Team",
        "team_abbrev": team.get("abbreviation"),
        "slot_type": round_label.lower().replace(" ", "_") or "unknown",
        "pick_value": _to_float(pick.get("pickValue")),
        "bonus_pool_value": None,
        "compensation_for": None,
        "acquired_from": None,
        "notes": None,
        "source": STATS_API_SOURCE,
        "source_url": DRAFT_URL_TMPL.format(year=draft_year),
        "raw_payload": json.dumps(pick, ensure_ascii=False, sort_keys=True),
    }


def pick_to_raw_prospect(pick: dict[str, Any]) -> dict[str, Any] | None:
    """Build a normalize_prospect_row()-compatible raw dict from a drafted
    pick. Returns None for picks with no player yet (not drafted, or a
    passed pick) - there is nothing prospect-shaped to record."""
    person = pick.get("person")
    if not person or pick.get("isPass"):
        return None
    school = pick.get("school") or {}
    home = pick.get("home") or {}
    position = person.get("primaryPosition") or {}
    bat_side = person.get("batSide") or {}
    pitch_hand = person.get("pitchHand") or {}
    team = pick.get("team") or {}
    draft_type = pick.get("draftType") or {}
    return {
        "person_id": person.get("id"),
        "bis_player_id": pick.get("bisPlayerId"),
        "person_full_name": person.get("fullName"),
        "person_first_name": person.get("firstName"),
        "person_last_name": person.get("lastName"),
        "person_use_name": person.get("useName"),
        "person_use_last_name": person.get("useLastName"),
        "rank": pick.get("rank"),
        "person_primary_position_code": position.get("code"),
        "person_primary_position_name": position.get("name"),
        "person_primary_position_type": position.get("type"),
        "person_primary_position_abbreviation": position.get("abbreviation"),
        "person_bat_side_code": bat_side.get("code"),
        "person_pitch_hand_code": pitch_hand.get("code"),
        "school_name": school.get("name"),
        "school_school_class": school.get("schoolClass"),
        "school_state": school.get("state"),
        "school_country": school.get("country"),
        "home_city": home.get("city"),
        "home_state": home.get("state"),
        "home_country": home.get("country"),
        "person_birth_date": person.get("birthDate"),
        "person_current_age": person.get("currentAge"),
        "person_birth_city": person.get("birthCity"),
        "person_birth_state_province": person.get("birthStateProvince"),
        "person_birth_country": person.get("birthCountry"),
        "person_height": person.get("height"),
        "person_weight": person.get("weight"),
        "person_active": person.get("active"),
        "headshot_link": pick.get("headshotLink"),
        "scouting_report": pick.get("scoutingReport"),
        "blurb": pick.get("blurb"),
        "draft_type_code": draft_type.get("code"),
        "draft_type_description": draft_type.get("description"),
        "is_drafted": pick.get("isDrafted", False),
        "is_pass": pick.get("isPass", False),
        "pick_round": pick.get("pickRound"),
        "pick_number": pick.get("pickNumber"),
        "team_id": team.get("id"),
        "team_name": team.get("name"),
        "team_abbreviation": team.get("abbreviation"),
    }


def pick_to_actual_pick(pick: dict[str, Any], draft_year: int) -> dict[str, Any] | None:
    """Build an actual_picks-ready row. Returns None if the pick hasn't
    happened yet or was passed on. prospect_id is left unset here - the
    caller fills it in after upserting the corresponding prospect row."""
    person = pick.get("person")
    if not person or pick.get("isPass") or not pick.get("isDrafted"):
        return None
    team = pick.get("team") or {}
    school = pick.get("school") or {}
    position = person.get("primaryPosition") or {}
    return {
        "draft_year": draft_year,
        "pick_number": pick["pickNumber"],
        "round_label": pick.get("pickRound"),
        "round_pick_number": pick.get("roundPickNumber"),
        "team_id": team.get("id"),
        "team_name": team.get("name") or "Unknown Team",
        "team_abbrev": team.get("abbreviation"),
        "prospect_id": None,
        "mlb_person_id": person.get("id"),
        "player_name": person.get("fullName"),
        "player_position": position.get("name"),
        "school_name": school.get("name"),
        "source": STATS_API_SOURCE,
        "source_event_id": f"{draft_year}:{pick['pickNumber']}:{person.get('id')}",
        "picked_at": None,
        "signed_status": None,
        "bonus_amount": _to_float(pick.get("signingBonus")),
        "slot_value": _to_float(pick.get("pickValue")),
        "raw_payload": json.dumps(pick, ensure_ascii=False, sort_keys=True),
    }


def sync_draft_order(conn, draft_year: int, client: HttpClient | None = None) -> list[dict[str, Any]]:
    """Fetch the full draft scaffold (every round's picks, drafted or not)
    and upsert draft_slots - the pick-by-pick schedule with team + slot
    value, independent of whether picks have happened yet."""
    payload = fetch_draft(draft_year, client=client)
    rows = [pick_to_draft_slot(pick, draft_year) for pick in iter_picks(payload)]
    for row in rows:
        upsert_draft_slot(conn, row)
    return rows


def reconcile_picks_from_api(
    conn,
    draft_year: int = 2026,
    notifier: TelegramNotifier | None = None,
    client: HttpClient | None = None,
) -> list[dict[str, Any]]:
    """Full reconciliation against the live draft: for every pick that has
    actually happened, upsert the drafted player as a prospect and as an
    actual_pick, and fire a Telegram alert for any pick not seen before."""
    notifier = notifier or TelegramNotifier()
    payload = fetch_draft(draft_year, client=client)
    new_picks: list[dict[str, Any]] = []

    for pick in iter_picks(payload):
        raw = pick_to_raw_prospect(pick)
        if raw is None:
            continue

        normalized = normalize_prospect_row(raw, draft_year)
        normalized["source"] = STATS_API_SOURCE
        upsert_prospect(conn, normalized)

        pick_row = pick_to_actual_pick(pick, draft_year)
        if pick_row is None:
            continue

        existing = conn.execute(
            "SELECT * FROM actual_picks WHERE draft_year = ? AND pick_number = ?",
            (draft_year, pick_row["pick_number"]),
        ).fetchone()
        prospect_row = conn.execute(
            "SELECT prospect_id FROM prospects WHERE mlb_person_id = ? AND draft_year = ?",
            (pick_row["mlb_person_id"], draft_year),
        ).fetchone()
        pick_row["prospect_id"] = prospect_row["prospect_id"] if prospect_row else None

        upsert_actual_pick(conn, pick_row)
        if existing is None:
            new_picks.append(pick_row)
            send_pick_if_new(conn, notifier, draft_year, pick_row)

    return new_picks
