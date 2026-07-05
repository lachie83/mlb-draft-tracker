from __future__ import annotations

import csv
import json
import re
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .clients import MLB_DRAFT_ORDER_URL
from .db import dump_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = PROJECT_ROOT / "examples"
REQUIRED_R_PACKAGES = ("baseballr", "DBI", "RSQLite", "jsonlite")
INSTALL_R_PACKAGES_COMMAND = (
    f"Rscript -e \"install.packages(c({','.join(repr(pkg) for pkg in REQUIRED_R_PACKAGES)}))\""
)


def rscript_missing_message() -> str:
    return (
        "Rscript not found on PATH.\n"
        "Install R, then install required packages:\n"
        f"{INSTALL_R_PACKAGES_COMMAND}"
    )


def rscript_missing_with_no_r_fallback_message() -> str:
    return (
        f"{rscript_missing_message()}\n"
        "You can still use no-R mode with "
        "`python3 main.py seed-no-r-prospects --year 2026`."
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def has_rscript() -> bool:
    try:
        result = subprocess.run(["Rscript", "--version"], capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def run_rscript(code: str) -> str:
    try:
        result = subprocess.run(
            ["Rscript", "-e", code],
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout
    except FileNotFoundError as exc:
        raise RuntimeError(rscript_missing_with_no_r_fallback_message()) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        match = re.search(r"there is no package called ['\"](.+?)['\"]", stderr)
        if match:
            pkg = match.group(1)
            raise RuntimeError(
                f"R package '{pkg}' is missing. Install required packages with:\n"
                f"{INSTALL_R_PACKAGES_COMMAND}"
            ) from exc
        raise RuntimeError(
            "Rscript command failed while running baseballr integration. "
            "Run `python3 main.py verify-baseballr` to diagnose setup.\n"
            f"R error output:\n{stderr or '(no stderr output)'}"
        ) from exc


def verify_baseballr_setup() -> tuple[bool, str]:
    if not has_rscript():
        return (False, rscript_missing_message())

    package_list = ",".join(f"'{pkg}'" for pkg in REQUIRED_R_PACKAGES)
    check_code = textwrap.dedent(
        f"""
        pkgs <- c({package_list})
        missing <- pkgs[!vapply(pkgs, requireNamespace, logical(1), quietly = TRUE)]
        if (length(missing) > 0) {{
          cat(paste(missing, collapse = ","), sep = "")
          quit(status = 2)
        }}
        cat("ok")
        """
    )
    try:
        result = subprocess.run(["Rscript", "-e", check_code], text=True, capture_output=True)
    except FileNotFoundError:
        return (False, rscript_missing_message())
    if result.returncode == 0 and result.stdout.strip() == "ok":
        return (True, f"R + baseballr path is ready (packages: {', '.join(REQUIRED_R_PACKAGES)}).")

    missing_csv = (result.stdout or "").strip()
    if missing_csv:
        missing = ", ".join(missing_csv.split(","))
        return (
            False,
            "Missing R package(s): "
            f"{missing}\n"
            "Install required packages with:\n"
            f"{INSTALL_R_PACKAGES_COMMAND}",
        )

    stderr = (result.stderr or "").strip()
    return (
        False,
        "Unable to verify R package setup.\n"
        "Run `Rscript --version` and then install required packages with:\n"
        f"{INSTALL_R_PACKAGES_COMMAND}\n"
        f"R error output:\n{stderr or '(no stderr output)'}",
    )


def fetch_baseballr_prospects_csv(year: int) -> list[dict[str, Any]]:
    if not has_rscript():
        raise RuntimeError(rscript_missing_with_no_r_fallback_message())
    code = f'''
    suppressPackageStartupMessages({{
      library(baseballr)
      library(jsonlite)
    }})
    x <- mlb_draft_prospects(year = {year})
    cat(jsonlite::toJSON(x, dataframe = "rows", auto_unbox = TRUE, null = "null", na = "null"))
    '''
    output = run_rscript(code)
    return json.loads(output)


def normalize_prospect_row(row: dict[str, Any], draft_year: int) -> dict[str, Any]:
    return {
        "mlb_person_id": row.get("person_id"),
        "bis_player_id": row.get("bis_player_id"),
        "full_name": row.get("person_full_name") or row.get("person_first_last_name") or "UNKNOWN",
        "first_name": row.get("person_first_name"),
        "last_name": row.get("person_last_name"),
        "use_name": row.get("person_use_name"),
        "use_last_name": row.get("person_use_last_name"),
        "rank": row.get("rank"),
        "position_code": row.get("person_primary_position_code"),
        "position_name": row.get("person_primary_position_name"),
        "position_type": row.get("person_primary_position_type"),
        "position_abbreviation": row.get("person_primary_position_abbreviation"),
        "bats": row.get("person_bat_side_code"),
        "throws": row.get("person_pitch_hand_code"),
        "school_name": row.get("school_name"),
        "school_class": row.get("school_school_class"),
        "school_state": row.get("school_state"),
        "school_country": row.get("school_country"),
        "home_city": row.get("home_city"),
        "home_state": row.get("home_state"),
        "home_country": row.get("home_country"),
        "birth_date": row.get("person_birth_date"),
        "current_age": row.get("person_current_age"),
        "birth_city": row.get("person_birth_city"),
        "birth_state_province": row.get("person_birth_state_province"),
        "birth_country": row.get("person_birth_country"),
        "height": row.get("person_height"),
        "weight": row.get("person_weight"),
        "active": int(bool(row.get("person_active"))) if row.get("person_active") is not None else None,
        "headshot_link": row.get("headshot_link"),
        "scouting_report": row.get("scouting_report"),
        "blurb": row.get("blurb"),
        "draft_year": draft_year,
        "draft_type_code": row.get("draft_type_code"),
        "draft_type_description": row.get("draft_type_description"),
        "is_drafted": int(bool(row.get("is_drafted"))) if row.get("is_drafted") is not None else 0,
        "is_pass": int(bool(row.get("is_pass"))) if row.get("is_pass") is not None else 0,
        "pick_round": row.get("pick_round"),
        "pick_number": row.get("pick_number"),
        "draft_team_id": row.get("team_id"),
        "draft_team_name": row.get("team_name"),
        "draft_team_abbreviation": row.get("team_abbreviation"),
        "source": "baseballr_mlb_draft_prospects",
        "source_rank_updated_at": utc_now(),
        "source_pick_updated_at": utc_now(),
        "raw_payload": dump_json(row),
    }


def seed_draft_slots_from_csv(csv_path: Path, draft_year: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for src in reader:
            rows.append(
                {
                    "draft_year": draft_year,
                    "round_label": src["round_label"],
                    "round_pick_number": int(src["round_pick_number"]) if src.get("round_pick_number") else None,
                    "pick_number": int(src["pick_number"]),
                    "team_id": int(src["team_id"]) if src.get("team_id") else None,
                    "team_name": src["team_name"],
                    "team_abbrev": src.get("team_abbrev"),
                    "slot_type": src.get("slot_type") or src["round_label"].lower().replace(" ", "_"),
                    "pick_value": float(src["pick_value"]) if src.get("pick_value") else None,
                    "bonus_pool_value": float(src["bonus_pool_value"]) if src.get("bonus_pool_value") else None,
                    "compensation_for": src.get("compensation_for"),
                    "acquired_from": src.get("acquired_from"),
                    "notes": src.get("notes"),
                    "source": "seed_csv",
                    "source_url": MLB_DRAFT_ORDER_URL,
                    "raw_payload": dump_json(src),
                }
            )
    return rows


PROSPECT_CSV_SOURCE = "mlb_pipeline_draft_prospects_manual_csv"

_INFIELD_ABBREVIATIONS = {"1B", "2B", "3B", "SS"}
_PITCHER_ABBREVIATIONS = {"RHP", "LHP"}
_SINGLE_POSITION_NAMES = {
    "1B": "First Base",
    "2B": "Second Base",
    "3B": "Third Base",
    "SS": "Shortstop",
    "C": "Catcher",
    "OF": "Outfield",
    "RHP": "Pitcher",
    "LHP": "Pitcher",
}


def expand_position_name(position_abbreviation: str) -> str:
    """Map a scraped position abbreviation (e.g. 'SS', 'LHP/OF') to the
    canonical position_name categories used elsewhere in the app."""
    parts = position_abbreviation.split("/")
    if len(parts) == 1:
        return _SINGLE_POSITION_NAMES.get(position_abbreviation, position_abbreviation)
    parts_set = set(parts)
    if parts_set & _PITCHER_ABBREVIATIONS and parts_set - _PITCHER_ABBREVIATIONS:
        return "Two-Way Player"
    if parts_set <= _INFIELD_ABBREVIATIONS:
        return "Infield"
    return _SINGLE_POSITION_NAMES.get(parts[0], parts[0])


def classify_school_class(school_name: str) -> str:
    """Coarse HS / JC / 4YR bucket derived from the school name alone.

    MLB Pipeline's draft rankings page doesn't expose a class-year (e.g. "HS SR",
    "4YR JR") like baseballr does, only the school name, so this is intentionally
    approximate: high schools are conventionally suffixed with a state/province
    abbreviation in parentheses (e.g. "Fort Worth Christian (TX)")."""
    if re.search(r"\([A-Za-z.\s]+\)$", school_name):
        return "HS"
    if re.search(r"\bCC\b|Community College|Junior College", school_name):
        return "JC"
    return "4YR"


def parse_feet_inches_height(height_weight: str) -> tuple[str | None, int | None]:
    """Parse a "6' 3\" / 185 lbs" cell into ("6-3", 185)."""
    m = re.match(r"^(\d+)'\s*(\d+)\"\s*/\s*(\d+)\s*lbs$", height_weight.strip())
    if not m:
        return None, None
    feet, inches, weight = m.groups()
    return f"{feet}-{inches}", int(weight)


def seed_prospects_from_csv(csv_path: Path, draft_year: int, id_base: int = 940000) -> list[dict[str, Any]]:
    """Load a manually-curated/scraped prospect board CSV (rank, full_name,
    position_abbreviation, school_name, age, height, weight, bats, throws)
    for use when baseballr is unavailable. person_id values are synthetic
    (id_base + rank) since this source doesn't expose real MLB person ids."""
    rows: list[dict[str, Any]] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for src in reader:
            rank = int(src["rank"])
            full_name = src["full_name"].strip()
            position_abbreviation = src["position_abbreviation"].strip()
            raw = {
                "person_id": id_base + rank,
                "person_full_name": full_name,
                "person_first_name": full_name.split()[0],
                "person_last_name": full_name.split()[-1],
                "rank": rank,
                "person_primary_position_abbreviation": position_abbreviation,
                "person_primary_position_name": expand_position_name(position_abbreviation),
                "school_name": src["school_name"].strip(),
                "school_school_class": classify_school_class(src["school_name"].strip()),
                "person_current_age": int(src["age"]) if src.get("age") else None,
                "person_height": src.get("height") or None,
                "person_weight": int(src["weight"]) if src.get("weight") else None,
                "person_bat_side_code": src.get("bats") or None,
                "person_pitch_hand_code": src.get("throws") or None,
                "is_drafted": False,
                "is_pass": False,
                "pick_round": None,
                "pick_number": None,
                "team_id": None,
                "team_name": None,
                "team_abbreviation": None,
                "person_active": True,
                "blurb": "Seeded from MLB Pipeline draft prospect rankings (manual CSV import).",
            }
            normalized = normalize_prospect_row(raw, draft_year)
            normalized["source"] = PROSPECT_CSV_SOURCE
            rows.append(normalized)
    return rows
