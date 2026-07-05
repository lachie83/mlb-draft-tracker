from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "mlb_draft_2026.db"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    ensure_parent(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_cursor(db_path: Path | str = DEFAULT_DB_PATH):
    conn = get_connection(db_path)
    cur = conn.cursor()
    try:
        yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def init_db(db_path: Path | str = DEFAULT_DB_PATH, schema_path: Path | str = SCHEMA_PATH) -> None:
    schema = Path(schema_path).read_text()
    with db_cursor(db_path) as (conn, _):
        conn.executescript(schema)


def upsert_prospect(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    sql = """
    INSERT INTO prospects (
        mlb_person_id, bis_player_id, full_name, first_name, last_name, use_name, use_last_name,
        rank, position_code, position_name, position_type, position_abbreviation, bats, throws,
        school_name, school_class, school_state, school_country,
        home_city, home_state, home_country, birth_date, current_age,
        birth_city, birth_state_province, birth_country, height, weight, active,
        headshot_link, scouting_report, blurb, draft_year, draft_type_code, draft_type_description,
        is_drafted, is_pass, pick_round, pick_number, draft_team_id, draft_team_name,
        draft_team_abbreviation, source, source_rank_updated_at, source_pick_updated_at, raw_payload, updated_at
    ) VALUES (
        :mlb_person_id, :bis_player_id, :full_name, :first_name, :last_name, :use_name, :use_last_name,
        :rank, :position_code, :position_name, :position_type, :position_abbreviation, :bats, :throws,
        :school_name, :school_class, :school_state, :school_country,
        :home_city, :home_state, :home_country, :birth_date, :current_age,
        :birth_city, :birth_state_province, :birth_country, :height, :weight, :active,
        :headshot_link, :scouting_report, :blurb, :draft_year, :draft_type_code, :draft_type_description,
        :is_drafted, :is_pass, :pick_round, :pick_number, :draft_team_id, :draft_team_name,
        :draft_team_abbreviation, :source, :source_rank_updated_at, :source_pick_updated_at, :raw_payload, CURRENT_TIMESTAMP
    )
    ON CONFLICT(mlb_person_id, draft_year) DO UPDATE SET
        bis_player_id=excluded.bis_player_id,
        full_name=excluded.full_name,
        first_name=excluded.first_name,
        last_name=excluded.last_name,
        use_name=excluded.use_name,
        use_last_name=excluded.use_last_name,
        rank=excluded.rank,
        position_code=excluded.position_code,
        position_name=excluded.position_name,
        position_type=excluded.position_type,
        position_abbreviation=excluded.position_abbreviation,
        bats=excluded.bats,
        throws=excluded.throws,
        school_name=excluded.school_name,
        school_class=excluded.school_class,
        school_state=excluded.school_state,
        school_country=excluded.school_country,
        home_city=excluded.home_city,
        home_state=excluded.home_state,
        home_country=excluded.home_country,
        birth_date=excluded.birth_date,
        current_age=excluded.current_age,
        birth_city=excluded.birth_city,
        birth_state_province=excluded.birth_state_province,
        birth_country=excluded.birth_country,
        height=excluded.height,
        weight=excluded.weight,
        active=excluded.active,
        headshot_link=excluded.headshot_link,
        scouting_report=excluded.scouting_report,
        blurb=excluded.blurb,
        draft_type_code=excluded.draft_type_code,
        draft_type_description=excluded.draft_type_description,
        is_drafted=excluded.is_drafted,
        is_pass=excluded.is_pass,
        pick_round=excluded.pick_round,
        pick_number=excluded.pick_number,
        draft_team_id=excluded.draft_team_id,
        draft_team_name=excluded.draft_team_name,
        draft_team_abbreviation=excluded.draft_team_abbreviation,
        source=excluded.source,
        source_rank_updated_at=excluded.source_rank_updated_at,
        source_pick_updated_at=excluded.source_pick_updated_at,
        raw_payload=excluded.raw_payload,
        updated_at=CURRENT_TIMESTAMP
    """
    conn.execute(sql, row)


def insert_or_replace_predictions(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]], draft_year: int, model_version: str) -> None:
    conn.execute(
        "DELETE FROM predictions WHERE draft_year = ? AND model_version = ?",
        (draft_year, model_version),
    )
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
    conn.executemany(sql, rows)


def upsert_mock_draft_pick(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    sql = """
    INSERT INTO mock_draft_picks (
        draft_year, source_name, source_authors, source_date, source_url, weight,
        pick_number, team_name, player_name, prospect_id, mlb_person_id, board_rank, notes
    ) VALUES (
        :draft_year, :source_name, :source_authors, :source_date, :source_url, :weight,
        :pick_number, :team_name, :player_name, :prospect_id, :mlb_person_id, :board_rank, :notes
    )
    ON CONFLICT(draft_year, source_name, source_date, pick_number, player_name) DO UPDATE SET
        source_authors=excluded.source_authors,
        source_url=excluded.source_url,
        weight=excluded.weight,
        team_name=excluded.team_name,
        prospect_id=excluded.prospect_id,
        mlb_person_id=excluded.mlb_person_id,
        board_rank=excluded.board_rank,
        notes=excluded.notes
    """
    conn.execute(sql, row)


def upsert_draft_slot(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    sql = """
    INSERT INTO draft_slots (
        draft_year, round_label, round_pick_number, pick_number, team_id, team_name,
        team_abbrev, slot_type, pick_value, bonus_pool_value, compensation_for,
        acquired_from, notes, source, source_url, raw_payload, updated_at
    ) VALUES (
        :draft_year, :round_label, :round_pick_number, :pick_number, :team_id, :team_name,
        :team_abbrev, :slot_type, :pick_value, :bonus_pool_value, :compensation_for,
        :acquired_from, :notes, :source, :source_url, :raw_payload, CURRENT_TIMESTAMP
    )
    ON CONFLICT(draft_year, pick_number) DO UPDATE SET
        round_label=excluded.round_label,
        round_pick_number=excluded.round_pick_number,
        team_id=excluded.team_id,
        team_name=excluded.team_name,
        team_abbrev=excluded.team_abbrev,
        slot_type=excluded.slot_type,
        pick_value=excluded.pick_value,
        bonus_pool_value=excluded.bonus_pool_value,
        compensation_for=excluded.compensation_for,
        acquired_from=excluded.acquired_from,
        notes=excluded.notes,
        source=excluded.source,
        source_url=excluded.source_url,
        raw_payload=excluded.raw_payload,
        updated_at=CURRENT_TIMESTAMP
    """
    conn.execute(sql, row)


def upsert_actual_pick(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    sql = """
    INSERT INTO actual_picks (
        draft_year, pick_number, round_label, round_pick_number, team_id, team_name, team_abbrev,
        prospect_id, mlb_person_id, player_name, player_position, school_name, source,
        source_event_id, picked_at, signed_status, bonus_amount, slot_value, raw_payload, updated_at
    ) VALUES (
        :draft_year, :pick_number, :round_label, :round_pick_number, :team_id, :team_name, :team_abbrev,
        :prospect_id, :mlb_person_id, :player_name, :player_position, :school_name, :source,
        :source_event_id, :picked_at, :signed_status, :bonus_amount, :slot_value, :raw_payload, CURRENT_TIMESTAMP
    )
    ON CONFLICT(draft_year, pick_number) DO UPDATE SET
        round_label=excluded.round_label,
        round_pick_number=excluded.round_pick_number,
        team_id=excluded.team_id,
        team_name=excluded.team_name,
        team_abbrev=excluded.team_abbrev,
        prospect_id=excluded.prospect_id,
        mlb_person_id=excluded.mlb_person_id,
        player_name=excluded.player_name,
        player_position=excluded.player_position,
        school_name=excluded.school_name,
        source=excluded.source,
        source_event_id=excluded.source_event_id,
        picked_at=excluded.picked_at,
        signed_status=excluded.signed_status,
        bonus_amount=excluded.bonus_amount,
        slot_value=excluded.slot_value,
        raw_payload=excluded.raw_payload,
        updated_at=CURRENT_TIMESTAMP
    """
    conn.execute(sql, row)


def get_best_available(conn: sqlite3.Connection, draft_year: int, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM prospects
        WHERE draft_year = ?
          AND rank IS NOT NULL
          AND COALESCE(is_drafted, 0) = 0
        ORDER BY rank
        LIMIT ?
        """,
        (draft_year, limit),
    ).fetchall()


def get_top_prospects(conn: sqlite3.Connection, draft_year: int, limit: int = 250) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM prospects WHERE draft_year = ? AND rank IS NOT NULL ORDER BY rank LIMIT ?",
        (draft_year, limit),
    ).fetchall()


def get_sent_event(conn: sqlite3.Connection, event_key: str):
    return conn.execute(
        "SELECT * FROM telegram_events_sent WHERE event_key = ?",
        (event_key,),
    ).fetchone()


def mark_event_sent(conn: sqlite3.Connection, event_key: str, payload_hash: str, pick_number: int | None, message_text: str) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO telegram_events_sent (event_key, payload_hash, pick_number, message_text)
        VALUES (?, ?, ?, ?)
        """,
        (event_key, payload_hash, pick_number, message_text),
    )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(r) for r in rows if r is not None]


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
