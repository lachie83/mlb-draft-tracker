from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any


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


def seed_curated_mock_assignments() -> list[dict[str, Any]]:
    return [
        {'pick_number': 1, 'team_name': 'Chicago White Sox', 'player_name': 'Roch Cholowsky', 'weight': 3.0, 'source': 'MLB Pipeline July 2 + June 25 + historical mock consensus'},
        {'pick_number': 2, 'team_name': 'Tampa Bay Rays', 'player_name': 'Grady Emerson', 'weight': 2.0, 'source': 'MLB Pipeline July 2'},
        {'pick_number': 2, 'team_name': 'Tampa Bay Rays', 'player_name': 'Vahn Lackey', 'weight': 1.0, 'source': 'MLB Pipeline June 25'},
        {'pick_number': 3, 'team_name': 'Minnesota Twins', 'player_name': 'Vahn Lackey', 'weight': 2.0, 'source': 'MLB Pipeline July 2'},
        {'pick_number': 3, 'team_name': 'Minnesota Twins', 'player_name': 'Grady Emerson', 'weight': 1.0, 'source': 'MLB Pipeline June 25'},
        {'pick_number': 4, 'team_name': 'San Francisco Giants', 'player_name': 'Jacob Lombard', 'weight': 3.0, 'source': 'MLB Pipeline July 2 + June 25'},
        {'pick_number': 5, 'team_name': 'Pittsburgh Pirates', 'player_name': 'Jackson Flora', 'weight': 1.5, 'source': 'heuristic extension from top board'},
        {'pick_number': 6, 'team_name': 'Kansas City Royals', 'player_name': 'Drew Burress', 'weight': 1.2, 'source': 'heuristic extension from top board'},
        {'pick_number': 7, 'team_name': 'Baltimore Orioles', 'player_name': 'Eric Booth Jr.', 'weight': 1.2, 'source': 'heuristic extension from top board'},
        {'pick_number': 8, 'team_name': 'Athletics', 'player_name': 'Gio Rojas', 'weight': 1.1, 'source': 'heuristic extension from top board'},
        {'pick_number': 9, 'team_name': 'Atlanta Braves', 'player_name': 'Justin Lebron', 'weight': 1.0, 'source': 'heuristic extension from top board'},
        {'pick_number': 10, 'team_name': 'Colorado Rockies', 'player_name': 'Tyler Bell', 'weight': 1.0, 'source': 'heuristic extension from top board'},
    ]
