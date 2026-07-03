from __future__ import annotations

import csv
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .clients import MLB_DRAFT_ORDER_URL
from .db import dump_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = PROJECT_ROOT / "examples"
REQUIRED_R_PACKAGES = ("baseballr", "DBI", "RSQLite", "jsonlite")


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
        raise RuntimeError(
            "Rscript was not found on PATH. Install R first, then install required packages "
            "(baseballr, DBI, RSQLite, jsonlite). You can still use no-R mode with "
            "`python3 main.py seed-no-r-prospects --year 2026`."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        match = re.search(r"there is no package called [\"'‘`](.+?)[\"'’`]", stderr)
        if match:
            pkg = match.group(1)
            raise RuntimeError(
                f"R package '{pkg}' is missing. Install required packages with:\n"
                "Rscript -e \"install.packages(c('baseballr','DBI','RSQLite','jsonlite'))\""
            ) from exc
        raise RuntimeError(
            "Rscript command failed while running baseballr integration. "
            "Run `python3 main.py verify-baseballr` to diagnose setup.\n"
            f"R error output:\n{stderr or '(no stderr output)'}"
        ) from exc


def verify_baseballr_setup() -> tuple[bool, str]:
    if not has_rscript():
        return (
            False,
            "Rscript not found on PATH.\n"
            "Install R, then install required packages:\n"
            "Rscript -e \"install.packages(c('baseballr','DBI','RSQLite','jsonlite'))\"",
        )

    package_list = ",".join(f"'{pkg}'" for pkg in REQUIRED_R_PACKAGES)
    check_code = f"""
    pkgs <- c({package_list})
    missing <- pkgs[!vapply(pkgs, requireNamespace, logical(1), quietly = TRUE)]
    if (length(missing) > 0) {{
      cat(paste(missing, collapse = ","), sep = "")
      quit(status = 2)
    }}
    cat("ok")
    """
    try:
        result = subprocess.run(["Rscript", "-e", check_code], text=True, capture_output=True)
    except FileNotFoundError:
        return (
            False,
            "Rscript not found on PATH.\n"
            "Install R, then install required packages:\n"
            "Rscript -e \"install.packages(c('baseballr','DBI','RSQLite','jsonlite'))\"",
        )
    if result.returncode == 0 and result.stdout.strip() == "ok":
        return True, f"R + baseballr path is ready (packages: {', '.join(REQUIRED_R_PACKAGES)})."

    missing_csv = (result.stdout or "").strip()
    if missing_csv:
        missing = ", ".join(pkg for pkg in missing_csv.split(",") if pkg)
        return (
            False,
            "Missing R package(s): "
            f"{missing}\n"
            "Install required packages with:\n"
            "Rscript -e \"install.packages(c('baseballr','DBI','RSQLite','jsonlite'))\"",
        )

    stderr = (result.stderr or "").strip()
    return (
        False,
        "Unable to verify R package setup.\n"
        "Run `Rscript --version` and then install required packages with:\n"
        "Rscript -e \"install.packages(c('baseballr','DBI','RSQLite','jsonlite'))\"\n"
        f"R error output:\n{stderr or '(no stderr output)'}",
    )


def fetch_baseballr_prospects_csv(year: int) -> list[dict[str, Any]]:
    if not has_rscript():
        raise RuntimeError(
            "Rscript is not installed. Install R and required packages "
            "(baseballr, DBI, RSQLite, jsonlite), then retry.\n"
            "To continue without R, use no-R mode:\n"
            "python3 main.py seed-no-r-prospects --year 2026"
        )
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
