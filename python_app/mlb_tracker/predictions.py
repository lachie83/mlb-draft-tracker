from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from typing import Any


def team_fit_score(team_name: str, position_name: str | None, school_class: str | None) -> float:
    score = 0.0
    if position_name in {"Shortstop", "Catcher", "Center Fielder", "Outfielder"}:
        score += 0.15
    if position_name in {"Right Fielder", "Third Base", "First Base"}:
        score += 0.05
    if position_name in {"Pitcher", "Right Handed Pitcher", "Left Handed Pitcher"}:
        score += 0.12
    if school_class and "HS" in school_class.upper():
        score += 0.03
    if school_class and "JR" in school_class.upper():
        score += 0.02
    if team_name in {"White Sox", "Rockies", "Marlins", "Athletics", "Nationals"}:
        score += 0.03
    return score


def score_prospect(rank: int | None) -> float:
    if rank is None or rank <= 0:
        return 0.01
    return 1.0 / math.sqrt(rank)


def normalize_probabilities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(max(r["combined_score"], 0.0001) for r in rows)
    for r in rows:
        r["predicted_probability"] = max(r["combined_score"], 0.0001) / total
    rows.sort(key=lambda x: x["predicted_probability"], reverse=True)
    return rows


def generate_predictions(conn: sqlite3.Connection, draft_year: int = 2026, top_n_per_pick: int = 5, max_pick: int = 50) -> list[dict[str, Any]]:
    slots = conn.execute(
        "SELECT pick_number, team_name FROM draft_slots WHERE draft_year = ? AND pick_number <= ? ORDER BY pick_number",
        (draft_year, max_pick),
    ).fetchall()
    prospects = conn.execute(
        """
        SELECT prospect_id, mlb_person_id, full_name, rank, position_name, school_class
        FROM prospects
        WHERE draft_year = ? AND rank IS NOT NULL AND COALESCE(is_drafted, 0) = 0
        ORDER BY rank
        LIMIT 80
        """,
        (draft_year,),
    ).fetchall()

    all_predictions: list[dict[str, Any]] = []
    for slot in slots:
        candidates: list[dict[str, Any]] = []
        for prospect in prospects[: min(40, max(10, slot["pick_number"] + 10))]:
            rank_score = score_prospect(prospect["rank"])
            mock_score = max(0.0, (50 - abs((prospect["rank"] or 999) - slot["pick_number"])) / 100.0)
            fit_score = team_fit_score(slot["team_name"], prospect["position_name"], prospect["school_class"])
            buzz_score = 0.05 if prospect["rank"] and prospect["rank"] <= 10 else 0.0
            combined = (rank_score * 0.55) + (mock_score * 0.25) + (fit_score * 0.15) + (buzz_score * 0.05)
            candidates.append(
                {
                    "draft_year": draft_year,
                    "pick_number": slot["pick_number"],
                    "team_name": slot["team_name"],
                    "prospect_id": prospect["prospect_id"],
                    "mlb_person_id": prospect["mlb_person_id"],
                    "player_name": prospect["full_name"],
                    "rank_score": rank_score,
                    "mock_score": mock_score,
                    "fit_score": fit_score,
                    "buzz_score": buzz_score,
                    "combined_score": combined,
                    "model_version": "heuristic_v1",
                    "prediction_source": "rank+mock_shape+team_fit+buzz",
                    "notes": f"heuristic candidate for pick {slot['pick_number']}",
                }
            )
        normalize_probabilities(candidates)
        for row in candidates[:top_n_per_pick]:
            row["raw_payload"] = json.dumps(row, ensure_ascii=False, sort_keys=True)
            all_predictions.append(row)
    return all_predictions
