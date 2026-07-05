from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from .db import upsert_mock_draft_pick
from .real_mock_drafts_2026 import load_real_mock_draft_picks


def ingest_mock_assignments(conn: sqlite3.Connection, assignments: list[dict[str, Any]], draft_year: int = 2026, top_n_per_pick: int = 5) -> list[dict[str, Any]]:
    prospects = conn.execute(
        "SELECT prospect_id, mlb_person_id, full_name, rank FROM prospects WHERE draft_year = ? ORDER BY rank",
        (draft_year,),
    ).fetchall()
    by_name = {row['full_name'].lower(): row for row in prospects}
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for a in assignments:
        grouped[a['pick_number']].append(a)

    out: list[dict[str, Any]] = []
    for pick_number, rows in grouped.items():
        total_weight = sum(r.get('weight', 1.0) for r in rows)
        for r in rows:
            p = by_name.get(r['player_name'].lower())
            out.append(
                {
                    'draft_year': draft_year,
                    'pick_number': pick_number,
                    'team_name': r['team_name'],
                    'prospect_id': p['prospect_id'] if p else None,
                    'mlb_person_id': p['mlb_person_id'] if p else None,
                    'player_name': r['player_name'],
                    'predicted_probability': r.get('weight', 1.0) / total_weight,
                    'rank_score': None,
                    'mock_score': r.get('weight', 1.0),
                    'fit_score': None,
                    'buzz_score': None,
                    'model_version': 'mock_consensus_v1',
                    'prediction_source': r.get('source', 'manual_mock_consensus'),
                    'notes': r.get('notes'),
                    'raw_payload': json.dumps(r, ensure_ascii=False, sort_keys=True),
                }
            )
    return out


def ingest_real_mock_draft_picks(conn: sqlite3.Connection, draft_year: int = 2026) -> list[dict[str, Any]]:
    """Load the real, dated mock draft picks from real_mock_drafts_2026,
    match each player against the prospects board, and upsert them into
    mock_draft_picks. Returns the upserted rows."""
    prospects = conn.execute(
        "SELECT prospect_id, mlb_person_id, full_name, rank FROM prospects WHERE draft_year = ?",
        (draft_year,),
    ).fetchall()
    by_name = {row["full_name"].lower(): row for row in prospects}
    by_rank = {row["rank"]: row for row in prospects if row["rank"] is not None}

    rows: list[dict[str, Any]] = []
    for pick in load_real_mock_draft_picks(draft_year):
        # A couple of source articles have a player name that doesn't quite
        # match the board (e.g. a byline typo); fall back to the board rank
        # the article itself cited for that player rather than dropping the
        # match, since editing the quoted name would lose source fidelity.
        p = by_name.get(pick["player_name"].lower()) or by_rank.get(pick["board_rank"])
        row = {
            "draft_year": pick["draft_year"],
            "source_name": pick["source_name"],
            "source_authors": pick["source_authors"],
            "source_date": pick["source_date"],
            "source_url": pick["source_url"],
            "weight": pick["weight"],
            "pick_number": pick["pick_number"],
            "team_name": pick["team_name"],
            "player_name": pick["player_name"],
            "prospect_id": p["prospect_id"] if p else None,
            "mlb_person_id": p["mlb_person_id"] if p else None,
            "board_rank": pick["board_rank"],
            "notes": pick["notes"],
        }
        upsert_mock_draft_pick(conn, row)
        rows.append(row)
    return rows


def generate_mock_consensus_predictions(conn: sqlite3.Connection, draft_year: int = 2026, top_n_per_pick: int = 5) -> list[dict[str, Any]]:
    """Aggregate mock_draft_picks into predictions: for each pick, combine
    weight across every source that named the same player (this is where
    repeated player/team associations across mocks get merged into one
    consensus signal), then normalize into a probability distribution."""
    picks = conn.execute(
        """
        SELECT pick_number, team_name, player_name, prospect_id, mlb_person_id,
               weight, source_name, source_date, board_rank
        FROM mock_draft_picks
        WHERE draft_year = ?
        ORDER BY pick_number
        """,
        (draft_year,),
    ).fetchall()

    grouped: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in picks:
        pick_number = row["pick_number"]
        key = row["player_name"].strip().lower()
        candidate = grouped[pick_number].setdefault(
            key,
            {
                "player_name": row["player_name"],
                "team_name": row["team_name"],
                "prospect_id": row["prospect_id"],
                "mlb_person_id": row["mlb_person_id"],
                "board_ranks": set(),
                "total_weight": 0.0,
                "sources": [],
            },
        )
        candidate["total_weight"] += row["weight"]
        candidate["sources"].append(f"{row['source_name']} ({row['source_date']})")
        if row["board_rank"] is not None:
            candidate["board_ranks"].add(row["board_rank"])

    out: list[dict[str, Any]] = []
    for pick_number, candidates in grouped.items():
        total = sum(c["total_weight"] for c in candidates.values()) or 1.0
        ranked = sorted(candidates.values(), key=lambda c: c["total_weight"], reverse=True)
        for c in ranked[:top_n_per_pick]:
            sources = sorted(set(c["sources"]))
            out.append(
                {
                    "draft_year": draft_year,
                    "pick_number": pick_number,
                    "team_name": c["team_name"],
                    "prospect_id": c["prospect_id"],
                    "mlb_person_id": c["mlb_person_id"],
                    "player_name": c["player_name"],
                    "predicted_probability": c["total_weight"] / total,
                    "rank_score": None,
                    "mock_score": c["total_weight"],
                    "fit_score": None,
                    "buzz_score": None,
                    "model_version": "mock_consensus_v2",
                    "prediction_source": "; ".join(sources),
                    "notes": f"aggregated from {len(c['sources'])} mock draft pick(s)"
                    + (f"; board rank(s): {', '.join(str(r) for r in sorted(c['board_ranks']))}" if c["board_ranks"] else ""),
                    "raw_payload": json.dumps(
                        {"sources": sources, "total_weight": c["total_weight"], "board_ranks": sorted(c["board_ranks"])},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                }
            )
    return out
