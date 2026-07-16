from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
from collections import defaultdict
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from mlb_tracker.db import DEFAULT_DB_PATH, get_connection, get_prospect_sources, init_db
from mlb_tracker.draft_schedule import (
    DRAFT_2026_LOCATION,
    DRAFT_2026_MILESTONES,
    current_milestone,
    next_milestone,
    recommended_poll_interval_seconds,
)
from mlb_tracker.telegram import format_pick_summary, format_pick_title, round_display_name

FAVICON_PATH = Path(__file__).resolve().parent / "static" / "favicon.ico"
FAVICON_BYTES = FAVICON_PATH.read_bytes()

# Baked in at Docker build time (see Dockerfile/Dockerfile.k8s's GIT_COMMIT
# build-arg) so the running page can show which commit is actually deployed.
# Read once at import time, not per-request - it never changes without a
# restart anyway.
GIT_COMMIT = os.environ.get("GIT_COMMIT", "unknown")

PROSPECT_SOURCE_LABELS = {
    "mlb_stats_api_prospects": "Live MLB API",
    "baseballr_mlb_draft_prospects": "baseballr",
    "mlb_pipeline_draft_prospects_manual_csv": "CSV snapshot",
    "no_r_pipeline_scrape": "No-R scrape",
}


def describe_prospect_source(sources: list[str]) -> str:
    if not sources:
        return "No data loaded"
    if len(sources) > 1:
        # Shouldn't happen once every sync path clears the board first
        # (db.clear_prospect_board) - surfaced rather than silently
        # picking one, since it means something wrote prospects without
        # going through the normal sync commands.
        labels = ", ".join(PROSPECT_SOURCE_LABELS.get(s, s) for s in sources)
        return f"Mixed sources ({labels})"
    return PROSPECT_SOURCE_LABELS.get(sources[0], sources[0])


def q(conn: sqlite3.Connection, sql: str, params=()):
    return conn.execute(sql, params).fetchall()


def esc(value):
    if value is None:
        return ""
    return html.escape(str(value))


def slugify(value) -> str:
    if value is None:
        return ""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower())
    return slug.strip("-")


def school_type(school_class):
    if not school_class:
        return "Unknown"
    sc = str(school_class).upper()
    if "HS" in sc:
        return "High School"
    if "JC" in sc:
        return "Junior College"
    if sc == "NS":
        return "Unknown"
    return "College"


# More granular than school_type() above: signability leverage differs a lot
# by class year even within "College" - a college senior has the least
# leverage and typically signs under slot, while a HS player or college
# sophomore has the most. Real school.schoolClass values observed from the
# live API: "HS SR", "4YR FR/SO/JR/SR", "4YR 5S" (fifth-year senior),
# "4YR GR" (grad student), "JC J1/J2/J3", "NS".
_CLASS_YEAR_LABELS = {"FR": "Fr", "SO": "So", "JR": "Jr", "SR": "Sr"}


def class_label(school_class):
    if not school_class:
        return "Unknown"
    sc = str(school_class).upper()
    if "HS" in sc:
        return "High School"
    if "JC" in sc:
        return "JUCO"
    if sc == "NS":
        return "Unknown"
    if sc == "4YR GR":
        return "Grad Student"
    if sc == "4YR 5S":
        return "College 5th Yr"
    year = sc.replace("4YR", "").strip()
    if year in _CLASS_YEAR_LABELS:
        return f"College {_CLASS_YEAR_LABELS[year]}"
    return "College"


SIGNABILITY_THRESHOLD = 20

# Rounds 11-20 have no assigned slot value (pick_value is 0), but a signing
# bonus for one of those picks still counts against the team's pool for
# whatever it pays above this threshold - confirmed via CBS Sports/MLB.com
# 2025 draft coverage. Without this, pool_used double-counts every dollar of
# every late-round bonus even though most of them are well under this
# threshold and shouldn't count against the pool at all - verified directly
# against the completed 2025 draft's real numbers: without this threshold,
# 28 of 30 teams appear to have busted the 5%-overage penalty tier (which
# costs a future 1st-round pick); real reporting confirms no team has ever
# exceeded that tier in the 13 drafts under this system.
POST_10TH_ROUND_BONUS_POOL_THRESHOLD = 150_000


def signability_tag(rank, pick_number, threshold=SIGNABILITY_THRESHOLD):
    """delta = pick_number - rank: positive means the player was still on
    the board well past their consensus rank ("Steal" - fell due to
    signability concerns, over-slot bonus demands, or a crowded position),
    negative means the team picked them well ahead of consensus ("Reach" -
    often signals a pre-arranged under-slot deal freeing bonus-pool money
    elsewhere). Returns (None, None) when rank isn't known (unranked
    player) since there's nothing to compare the pick number against."""
    if rank is None:
        return None, None
    delta = pick_number - rank
    if delta > threshold:
        return "Steal", delta
    if delta < -threshold:
        return "Reach", delta
    return None, delta


# High-confidence only: requires the explicit "committed to X" / "commitment
# to X" phrasing MLB's blurb text sometimes uses for HS prospects. Tested
# against all 107 ranked HS prospects in the live 2026 board - a looser
# pattern (matching "...recruit" phrasing too) hit ~60% but produced real
# garbage (e.g. "The Texas A&M" bleeding across an unrelated sentence).
# This stricter version dropped coverage to ~23% (25/107) but produced zero
# garbage on manual inspection - returns None (not a guess) for anything not
# clearly matched, since most blurbs (77% in testing) either don't mention a
# commitment at all or phrase it too loosely to trust.
_COMMITMENT_PATTERNS = [
    re.compile(r"committed to ([A-Z][a-zA-Z&]+(?: [A-Z][a-zA-Z&]+){0,2})\b"),
    re.compile(r"commitment to ([A-Z][a-zA-Z&]+(?: [A-Z][a-zA-Z&]+){0,2})\b"),
]


def extract_commitment_school(blurb):
    if not blurb:
        return None
    for pattern in _COMMITMENT_PATTERNS:
        match = pattern.search(blurb)
        if match:
            return match.group(1).strip()
    return None


# Hand-tagged "blue blood" programs (top-25ish by recent recruiting/signing
# track record) - not exhaustive, tunable. Unlisted schools default to
# "medium" in commitment_tier_for_school() below; this is about a HS
# player's *committed destination*, separate from the eligibility-leverage
# handling in compute_signability() (a player's *current* enrollment).
COMMITMENT_TIERS = {
    "Vanderbilt": "hard", "LSU": "hard", "Louisiana State": "hard",
    "Florida": "hard", "Texas": "hard", "Texas A&M": "hard",
    "Arkansas": "hard", "Tennessee": "hard", "Wake Forest": "hard",
    "Oregon State": "hard", "Clemson": "hard", "Florida State": "hard",
    "Miami": "hard", "Virginia": "hard", "North Carolina": "hard",
    "Duke": "hard", "Mississippi State": "hard", "Auburn": "hard",
    "Stanford": "hard", "UCLA": "hard", "Oklahoma": "hard",
    "South Carolina": "hard", "Texas Christian": "hard", "Louisville": "hard",
    "Kentucky": "medium", "Georgia": "medium", "Alabama": "medium",
    "Georgia Tech": "medium", "Oklahoma State": "medium", "Penn State": "medium",
    "Coastal Carolina": "medium", "Oregon": "medium",
}


def commitment_tier_for_school(school_name):
    if not school_name:
        return None
    if re.search(r"\b(JC|Junior College|Community College)\b", school_name, re.I):
        return "easy"
    return COMMITMENT_TIERS.get(school_name, "medium")


def prior_draft_return(conn: sqlite3.Connection, mlb_person_id, draft_year: int) -> bool:
    """True if this player was already drafted in an earlier year and didn't
    sign (returned to school) - a genuine signability signal, not a guess.
    Verified against real synced data: 11 real 2026 prospects were 2025
    draftees (e.g. Brendan Brock, a 2025 pick at #425, now ranked #112 in
    2026) - uses mlb_person_id, stable across years, and data this app
    already syncs. No new source needed."""
    if mlb_person_id is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM actual_picks WHERE mlb_person_id = ? AND draft_year < ? LIMIT 1",
        (mlb_person_id, draft_year),
    ).fetchone()
    return row is not None


# Hand-compiled from specific published reports, same pattern as
# real_mock_drafts_2026.py - not a live/scraped feed. Keyed by mlb_person_id
# since names can collide. Empty for now: researched specifically for this
# feature (searched for named-player over/under-slot bonus-demand reporting
# on the 2026 class) and found only soft, non-attributable team-strategy
# speculation - e.g. a Chicago Sun-Times piece on the White Sox's #1 pick
# decision (2026-07-08) explicitly states "no specific bonus figures or
# asking prices" for any of the three candidates discussed. Add entries here
# as concrete, citable reporting actually emerges (typically closer to/
# during the draft itself) - each entry should cite its source, matching
# the mock-draft module's convention.
REPORTED_BONUS_DEMANDS: dict[int, dict] = {
    # mlb_person_id: {"tier": "over_slot" | "at_slot" | "under_slot", "source": "...", "source_url": "...", "source_date": "..."}
}


ELIGIBILITY_SIGNABILITY_ADJ = {
    # No leverage left - near-auto-sign, teams routinely sign these well
    # under slot.
    "College Sr": 25, "College 5th Yr": 25, "Grad Student": 25,
    # Some leverage (one more season of eligibility possible), but real cost
    # to walking away increases each year.
    "College Jr": -10,
    # Real leverage: JUCO transfer rules make returning painless, and
    # current reporting specifically calls out draft-eligible sophomores as
    # having "the most negotiating power ... can return to school for
    # another season and lose minimal value." NOTE this fixes a sign error
    # in the source plan, which scored JUCO at +15 (near-auto-sign, like a
    # college senior) - backwards. This is about a player *currently
    # enrolled* at a JUCO/as an underclassman deciding whether to return,
    # not a HS player's *committed destination* (that's commitment_tier,
    # unchanged and separate).
    "College So": -15, "College Fr": -15, "JUCO": -15,
    # Highest variance - can go anywhere, real wildcard.
    "High School": -15,
}

SIGNABILITY_POOL_WARNING_PCT = 0.95
SIGNABILITY_POOL_COMFORTABLE_PCT = 0.60


def compute_signability(
    *, rank, pick_number, class_label_value, commitment_tier,
    pool_pct_used, prior_return=False, reported_bonus_tier=None,
):
    """0-100 signability score, higher = easier sign; tiered into Likely
    Sign / Moderate Risk / Tough Sign. Deliberately simple, linear point
    adjustments (not a trained model) so it's easy to explain in a tooltip
    and easy to retune once real 2026 signings start landing."""
    score = 70
    factors = []

    adj = ELIGIBILITY_SIGNABILITY_ADJ.get(class_label_value, 0)
    if adj:
        score += adj
        factors.append(f"{class_label_value} eligibility")

    if rank is not None:
        delta = pick_number - rank
        if delta > 15:
            score -= 15
            factors.append(f"fell {delta} spots past consensus rank")
        elif delta > 5:
            score -= 7
            factors.append(f"fell {delta} spots past consensus rank")
        elif delta < -10:
            score += 5
            factors.append("popped early relative to rank")

    if commitment_tier:
        commitment_adj = {"hard": -20, "medium": -8, "easy": 5}
        score += commitment_adj.get(commitment_tier, 0)
        factors.append(f"{commitment_tier} college commitment")

    if pool_pct_used is not None:
        if pool_pct_used > SIGNABILITY_POOL_WARNING_PCT:
            score -= 10
            factors.append("drafting team has little pool room left")
        elif pool_pct_used < SIGNABILITY_POOL_COMFORTABLE_PCT:
            score += 5
            factors.append("drafting team has ample pool room")

    if prior_return:
        score -= 15
        factors.append("previously drafted and didn't sign")

    if reported_bonus_tier:
        reported_adj = {"over_slot": -20, "at_slot": 0, "under_slot": 10}
        score += reported_adj.get(reported_bonus_tier, 0)
        factors.append(f"reported bonus demand: {reported_bonus_tier.replace('_', ' ')}")

    score = max(0, min(100, score))
    tier = "Likely Sign" if score >= 70 else "Moderate Risk" if score >= 40 else "Tough Sign"
    return {"score": score, "tier": tier, "factors": factors}


# MLB's official team IDs (matches what the MLB Stats API returns in
# draft_slots.team_id / actual_picks.team_id) - kept as a static lookup
# here too since several dashboard rows only carry team_name text (e.g.
# the mock_team join in Best Available comes from the predictions table,
# which has no team_id path back to a specific franchise record).
MLB_TEAM_IDS = {
    "Arizona Diamondbacks": 109,
    "Athletics": 133,
    "Atlanta Braves": 144,
    "Baltimore Orioles": 110,
    "Boston Red Sox": 111,
    "Chicago Cubs": 112,
    "Chicago White Sox": 145,
    "Cincinnati Reds": 113,
    "Cleveland Guardians": 114,
    "Colorado Rockies": 115,
    "Detroit Tigers": 116,
    "Houston Astros": 117,
    "Kansas City Royals": 118,
    "Los Angeles Angels": 108,
    "Los Angeles Dodgers": 119,
    "Miami Marlins": 146,
    "Milwaukee Brewers": 158,
    "Minnesota Twins": 142,
    "New York Mets": 121,
    "New York Yankees": 147,
    "Philadelphia Phillies": 143,
    "Pittsburgh Pirates": 134,
    "San Diego Padres": 135,
    "San Francisco Giants": 137,
    "Seattle Mariners": 136,
    "St. Louis Cardinals": 138,
    "Tampa Bay Rays": 139,
    "Texas Rangers": 140,
    "Toronto Blue Jays": 141,
    "Washington Nationals": 120,
}


def team_logo_url(team_name, variant="light"):
    team_id = MLB_TEAM_IDS.get(team_name)
    if not team_id:
        return None
    return f"https://www.mlbstatic.com/team-logos/team-cap-on-{variant}/{team_id}.svg"


def team_logo_html(team_name):
    dark_url = team_logo_url(team_name, "dark")
    light_url = team_logo_url(team_name, "light")
    if not dark_url or not light_url:
        return ""
    alt = esc(team_name)
    # MLB serves theme-matched logo variants directly - render both and let
    # CSS show the one matching the active theme, so the logo updates
    # instantly when the user toggles light/dark with no JS or re-fetch.
    return (
        f'<img class="team-logo team-logo-dark" src="{dark_url}" alt="{alt}" loading="lazy">'
        f'<img class="team-logo team-logo-light" src="{light_url}" alt="{alt}" loading="lazy">'
    )


TEAM_LOGO_HEADERS = {"Team", "Mock Team", "Drafted By"}
PLAYER_INFO_HEADERS = {"Player"}
TAG_HEADERS = {"Tag"}


def signability_tag_html(row) -> str:
    tag = row.get("signability_tag")
    delta = row.get("signability_delta")
    if not tag:
        return ""
    badge_class = "badge-accent" if tag == "Steal" else "badge-warning"
    sign = "+" if delta >= 0 else ""
    return f'<span class="badge {badge_class}">{esc(tag)} ({sign}{delta})</span>'


SIGNABILITY_HEADERS = {"Signability"}


def signability_badge_html(row) -> str:
    signability = row.get("signability")
    if not signability:
        return ""
    tier = signability["tier"]
    badge_class = {
        "Likely Sign": "badge-accent",
        "Moderate Risk": "badge-warning",
        "Tough Sign": "badge-warning",
    }.get(tier, "badge")
    return f'<span class="badge {badge_class}" title="{esc("; ".join(signability["factors"]) or "No notable factors")}">{esc(tier)} ({signability["score"]})</span>'


def prospect_info_button_html(row):
    """A small info button opening the floating scouting-report card,
    rendered only when there's actually something to show - tables that
    don't select any of these columns naturally get nothing here since
    row.get() just returns None."""
    blurb = row.get("blurb") or ""
    scouting = row.get("scouting_report") or ""
    position = row.get("position_name") or ""
    school = row.get("school_name") or ""
    if not blurb and not scouting and not position and not school:
        return ""
    name = esc(row.get("full_name") or "")
    bats, throws = row.get("bats"), row.get("throws")
    bt = f"{bats}/{throws}" if bats and throws else ""
    home_city, home_state = row.get("home_city"), row.get("home_state")
    hometown = ", ".join(p for p in (home_city, home_state) if p)
    bonus, slot = row.get("bonus_amount"), row.get("slot_value")
    over_under_str = ""
    if bonus is not None and slot is not None:
        delta = bonus - slot
        sign = "+" if delta >= 0 else "-"
        over_under_str = sign + format_usd(abs(delta))
    signability = row.get("signability")
    signability_tier = signability["tier"] if signability else ""
    signability_score = signability["score"] if signability else ""
    signability_factors = "|".join(signability["factors"]) if signability else ""
    return (
        f' <button type="button" class="prospect-info-btn" aria-label="Scouting info for {name}" '
        f'data-name="{name}" data-blurb="{esc(blurb)}" data-scouting="{esc(scouting)}" '
        f'data-headshot="{esc(row.get("headshot_link") or "")}" '
        f'data-position="{esc(position)}" data-school="{esc(school)}" data-bt="{esc(bt)}" '
        f'data-hometown="{esc(hometown)}" data-class="{esc(class_label(row.get("school_class")))}" '
        f'data-bonus="{esc(format_usd(bonus))}" data-slot="{esc(format_usd(slot))}" '
        f'data-over-under="{esc(over_under_str)}" '
        f'data-signability-tier="{esc(signability_tier)}" data-signability-score="{esc(signability_score)}" '
        f'data-signability-factors="{esc(signability_factors)}" '
        f'onclick="showProspectCard(this)">&#9432;</button>'
    )


def format_milestone_time(dt) -> str:
    return dt.strftime("%a %-I:%M %p ET")


def milestone_to_dict(m) -> dict:
    return {
        "key": m.key,
        "label": m.label,
        "picks_label": m.picks_label,
        "channels": m.channels,
        "start": m.start.isoformat(),
        "end": m.end.isoformat(),
        "poll_interval_seconds": m.poll_interval_seconds,
    }


def draft_milestone_payload(year: int, now=None) -> dict:
    """JSON payload for /api/draft-milestone - the schedule is specific to
    the real 2026 draft's wall-clock dates (draft_schedule.py), so any other
    year gets an empty payload rather than a schedule that doesn't apply.
    `now` is only ever overridden by tests; real requests let it default to
    the actual current time."""
    if year != 2026:
        return {"year": year, "location": None, "current": None, "next": None, "poll_interval_seconds": None, "milestones": []}
    current = current_milestone(now)
    nxt = next_milestone(now)
    return {
        "year": year,
        "location": DRAFT_2026_LOCATION,
        "current": milestone_to_dict(current) if current else None,
        "next": milestone_to_dict(nxt) if nxt else None,
        "poll_interval_seconds": recommended_poll_interval_seconds(now),
        "milestones": [milestone_to_dict(m) for m in DRAFT_2026_MILESTONES],
    }


def render_draft_schedule_banner(year: int, now=None) -> str:
    """Info box shown at the top of the 2026 dashboard with the real draft-day
    schedule - which window is live right now, when the next one starts, and
    the full timeline. Kept updated live by the client (pollDraftMilestone in
    SCRIPT) without a page reload, since a milestone can start while the tab
    sits open. `now` is only ever overridden by tests."""
    if year != 2026:
        return ""
    current = current_milestone(now)
    nxt = next_milestone(now)
    if current:
        status_html = (
            f'<span class="badge badge-accent">Live now</span> '
            f"{esc(current.label)} &middot; {esc(current.picks_label)} on {esc(current.channels)}"
        )
    elif nxt:
        status_html = (
            f'<span class="badge">Next up</span> '
            f"{esc(nxt.label)} starts {esc(format_milestone_time(nxt.start))} &middot; "
            f"{esc(nxt.picks_label)} on {esc(nxt.channels)}"
        )
    else:
        status_html = '<span class="badge">Complete</span> All scheduled draft windows have concluded.'

    milestone_chips = "".join(
        f'<div class="schedule-milestone{" active" if current and m.key == current.key else ""}" '
        f'data-milestone-key="{esc(m.key)}">'
        f'<span class="schedule-milestone-time">{esc(format_milestone_time(m.start))}&ndash;{esc(format_milestone_time(m.end))}</span>'
        f"{esc(m.label)} &middot; {esc(m.picks_label)}<br><span class=\"muted\">{esc(m.channels)}</span>"
        f"</div>"
        for m in DRAFT_2026_MILESTONES
    )

    return f"""
    <section class="schedule-banner" id="draft-schedule-banner">
      <div class="schedule-banner-header">
        <span class="schedule-banner-title">2026 Draft Schedule</span>
        <span class="schedule-banner-location">{esc(DRAFT_2026_LOCATION)}</span>
      </div>
      <div class="schedule-banner-status" id="schedule-banner-status">{status_html}</div>
      <div class="schedule-milestones">{milestone_chips}</div>
    </section>
    """


def get_selected_year(environ, default_year: int = 2026) -> int:
    try:
        params = parse_qs(environ.get("QUERY_STRING", ""))
        year_str = params.get("year", [str(default_year)])[0]
        return int(year_str)
    except Exception:
        return default_year


THEME_BG = {"dark": "#0b1120", "light": "#eef1f8"}


def get_selected_theme(environ):
    """The theme cookie set by toggleTheme(), or None on a first-ever visit
    with no cookie yet - callers fall back to a client-side prefers-color-
    scheme check in that case. Reading this server-side (rather than relying
    on client JS to mutate <meta name="theme-color"> after the fact) is what
    actually fixes iOS Safari's status-bar/notch overlay: that overlay is
    colored from the tag's value as parsed from the initial HTML response,
    not from later DOM mutations, so the value has to be correct from the
    first byte."""
    cookies = SimpleCookie()
    try:
        cookies.load(environ.get("HTTP_COOKIE", ""))
    except Exception:
        return None
    theme = cookies["mlb_theme"].value if "mlb_theme" in cookies else None
    return theme if theme in THEME_BG else None


def fetch_dashboard_data(conn: sqlite3.Connection, year: int):
    top_250 = [
        dict(r)
        for r in q(
            conn,
            """
            SELECT rank, full_name, position_name, school_name, school_class,
                   is_drafted, draft_team_name, pick_number, blurb, scouting_report,
                   bats, throws, home_city, home_state, headshot_link
            FROM prospects
            WHERE draft_year = ? AND rank IS NOT NULL
            ORDER BY rank
            LIMIT 250
            """,
            (year,),
        )
    ]

    best_available = [
        dict(r)
        for r in q(
            conn,
            """
            SELECT pros.rank, pros.full_name, pros.position_name, pros.school_name, pros.school_class,
                   pros.current_age, pros.bats, pros.throws, pros.blurb, pros.scouting_report,
                   pros.home_city, pros.home_state, pros.headshot_link,
                   mock.team_name AS mock_team, ROUND(mock.predicted_probability, 4) AS mock_probability
            FROM prospects pros
            LEFT JOIN (
                SELECT mlb_person_id, team_name, predicted_probability,
                       ROW_NUMBER() OVER (
                           PARTITION BY mlb_person_id ORDER BY predicted_probability DESC
                       ) AS rn
                FROM predictions
                WHERE draft_year = ? AND mlb_person_id IS NOT NULL
            ) mock ON mock.mlb_person_id = pros.mlb_person_id AND mock.rn = 1
            WHERE pros.draft_year = ? AND pros.rank IS NOT NULL AND COALESCE(pros.is_drafted, 0) = 0
            ORDER BY pros.rank
            LIMIT 25
            """,
            (year, year),
        )
    ]
    for row in best_available:
        row["bats_throws"] = (
            f"{row['bats']}/{row['throws']}" if row.get("bats") and row.get("throws") else ""
        )

    picks = [
        dict(r)
        for r in q(
            conn,
            """
            SELECT a.pick_number, a.team_name, a.player_name, a.player_position,
                   COALESCE(p.school_name, a.school_name) AS school_name,
                   a.bonus_amount, a.slot_value, a.mlb_person_id,
                   COALESCE(p.full_name, a.player_name) AS full_name,
                   COALESCE(p.position_name, a.player_position) AS position_name,
                   p.rank, p.blurb, p.scouting_report, p.bats, p.throws,
                   p.home_city, p.home_state, p.school_class, p.headshot_link
            FROM actual_picks a
            LEFT JOIN prospects p ON a.mlb_person_id = p.mlb_person_id AND a.draft_year = p.draft_year
            WHERE a.draft_year = ?
            ORDER BY a.pick_number
            LIMIT 100
            """,
            (year,),
        )
    ]
    pool_pct_used_by_team = {r["team_name"]: r["pct_used"] for r in fetch_team_pool_data(conn, year)}
    for row in picks:
        row["signed_status"] = "Signed" if row.get("bonus_amount") is not None else "Unsigned"
        row["signability_tag"], row["signability_delta"] = signability_tag(row.get("rank"), row["pick_number"])
        eligibility = class_label(row.get("school_class"))
        # Commitment tier only means anything for a current HS player -
        # college prospects' blurbs often reminisce about their *original*
        # HS commitment (e.g. Roch Cholowsky's blurb mentions his HS-era
        # commitment to UCLA, where he's since actually enrolled and become
        # a junior) - that's history, not a live decision, so applying it
        # here would misread a backward-looking fact as current leverage.
        commitment_tier = commitment_tier_for_school(extract_commitment_school(row.get("blurb"))) if eligibility == "High School" else None
        reported = REPORTED_BONUS_DEMANDS.get(row.get("mlb_person_id"))
        row["signability"] = compute_signability(
            rank=row.get("rank"), pick_number=row["pick_number"],
            class_label_value=eligibility,
            commitment_tier=commitment_tier,
            pool_pct_used=pool_pct_used_by_team.get(row["team_name"]),
            prior_return=prior_draft_return(conn, row.get("mlb_person_id"), year),
            reported_bonus_tier=reported["tier"] if reported else None,
        )

    predictions = [
        dict(r)
        for r in q(
            conn,
            """
            SELECT pr.pick_number, pr.team_name, pr.player_name,
                   COALESCE(p.full_name, pr.player_name) AS full_name,
                   ROUND(pr.predicted_probability, 4) AS predicted_probability,
                   pr.model_version, p.position_name, p.school_name, p.school_class,
                   p.blurb, p.scouting_report, p.bats, p.throws,
                   p.home_city, p.home_state, p.headshot_link
            FROM predictions pr
            LEFT JOIN prospects p
                ON pr.mlb_person_id = p.mlb_person_id AND pr.draft_year = p.draft_year
            WHERE pr.draft_year = ?
            ORDER BY pr.pick_number, pr.predicted_probability DESC
            LIMIT 100
            """,
            (year,),
        )
    ]

    prospects_loaded = q(
        conn,
        "SELECT COUNT(*) AS c FROM prospects WHERE draft_year = ?",
        (year,),
    )[0]["c"]

    picks_loaded = q(
        conn,
        "SELECT COUNT(*) AS c FROM actual_picks WHERE draft_year = ?",
        (year,),
    )[0]["c"]

    predictions_loaded = q(
        conn,
        "SELECT COUNT(*) AS c FROM predictions WHERE draft_year = ?",
        (year,),
    )[0]["c"]

    top_available_rows = q(
        conn,
        """
        SELECT full_name
        FROM prospects
        WHERE draft_year = ? AND rank IS NOT NULL AND COALESCE(is_drafted, 0) = 0
        ORDER BY rank
        LIMIT 1
        """,
        (year,),
    )

    picks_made_row = q(
        conn,
        "SELECT COUNT(*) AS c, COALESCE(MAX(pick_number), 0) AS max_pick FROM actual_picks WHERE draft_year = ?",
        (year,),
    )[0]
    picks_made = picks_made_row["max_pick"]

    fallers = []
    if picks_made > 0:
        fallers = [
            dict(r)
            for r in q(
                conn,
                """
                SELECT rank, full_name, position_name, school_name, school_class,
                       blurb, scouting_report, bats, throws, home_city, home_state, headshot_link,
                       (? - rank) AS picks_fallen
                FROM prospects
                WHERE draft_year = ? AND rank IS NOT NULL AND rank <= ?
                  AND COALESCE(is_drafted, 0) = 0
                ORDER BY picks_fallen DESC
                LIMIT 25
                """,
                (picks_made, year, picks_made),
            )
        ]

    on_the_clock = None
    next_pick_row = q(
        conn,
        """
        SELECT s.pick_number, s.team_name, s.round_label, s.pick_value
        FROM draft_slots s
        WHERE s.draft_year = ?
          AND s.pick_number NOT IN (
              SELECT pick_number FROM actual_picks WHERE draft_year = ?
          )
        ORDER BY s.pick_number
        LIMIT 1
        """,
        (year, year),
    )
    if next_pick_row:
        slot = next_pick_row[0]
        candidates = [
            dict(r)
            for r in q(
                conn,
                """
                SELECT pr.player_name,
                       ROUND(MAX(pr.predicted_probability), 4) AS predicted_probability,
                       COUNT(DISTINCT pr.model_version) AS model_count,
                       MAX(p.position_name) AS position_name, MAX(p.school_name) AS school_name
                FROM predictions pr
                LEFT JOIN prospects p
                    ON pr.mlb_person_id = p.mlb_person_id AND pr.draft_year = p.draft_year
                WHERE pr.draft_year = ? AND pr.pick_number = ?
                GROUP BY COALESCE(pr.mlb_person_id, pr.player_name)
                ORDER BY predicted_probability DESC
                LIMIT 5
                """,
                (year, slot["pick_number"]),
            )
        ]
        on_the_clock = {
            "pick_number": slot["pick_number"],
            "team_name": slot["team_name"],
            "round_label": slot["round_label"],
            "pick_value": slot["pick_value"],
            "candidates": candidates,
        }

    draft_order_rows = [
        dict(r)
        for r in q(
            conn,
            """
            SELECT s.pick_number, s.round_label, s.team_name, s.pick_value, ap.player_name,
                   COALESCE(p.position_name, ap.player_position) AS position_name,
                   COALESCE(p.full_name, ap.player_name) AS full_name,
                   p.blurb, p.scouting_report, p.school_name, p.bats, p.throws,
                   p.home_city, p.home_state, p.school_class, p.headshot_link
            FROM draft_slots s
            LEFT JOIN actual_picks ap
                ON ap.draft_year = s.draft_year AND ap.pick_number = s.pick_number
            LEFT JOIN prospects p
                ON ap.mlb_person_id = p.mlb_person_id AND ap.draft_year = p.draft_year
            WHERE s.draft_year = ?
            ORDER BY s.pick_number
            """,
            (year,),
        )
    ]
    round_first_pick: dict[str, int] = {}
    round_picks: dict[str, list[dict]] = defaultdict(list)
    for row in draft_order_rows:
        label = row["round_label"]
        round_first_pick.setdefault(label, row["pick_number"])
        round_picks[label].append(row)
    draft_order = [
        {"round_label": label, "round_name": round_display_name(label), "picks": round_picks[label]}
        for label in sorted(round_picks, key=lambda l: round_first_pick[l])
    ]

    positions = [
        r["position_name"]
        for r in q(
            conn,
            "SELECT DISTINCT position_name FROM prospects WHERE draft_year = ? AND position_name IS NOT NULL ORDER BY position_name",
            (year,),
        )
    ]

    teams = [
        r["team_name"]
        for r in q(
            conn,
            """
            SELECT team_name FROM draft_slots WHERE draft_year = ?
            UNION
            SELECT draft_team_name AS team_name FROM prospects WHERE draft_year = ? AND draft_team_name IS NOT NULL
            UNION
            SELECT team_name FROM actual_picks WHERE draft_year = ?
            ORDER BY team_name
            """,
            (year, year, year),
        )
    ]

    models = [
        r["model_version"]
        for r in q(
            conn,
            "SELECT DISTINCT model_version FROM predictions WHERE draft_year = ? ORDER BY model_version",
            (year,),
        )
    ]

    top_picks_by_model = [
        dict(r)
        for r in q(
            conn,
            """
            WITH ranked AS (
                SELECT pick_number, team_name, player_name, model_version, predicted_probability,
                       ROW_NUMBER() OVER (
                           PARTITION BY pick_number, model_version
                           ORDER BY predicted_probability DESC
                       ) AS rn
                FROM predictions
                WHERE draft_year = ?
            )
            SELECT pick_number, team_name, model_version, player_name,
                   ROUND(predicted_probability, 4) AS predicted_probability
            FROM ranked
            WHERE rn = 1
            ORDER BY pick_number
            """,
            (year,),
        )
    ]
    model_comparison: dict[int, dict] = {}
    for row in top_picks_by_model:
        entry = model_comparison.setdefault(
            row["pick_number"], {"pick_number": row["pick_number"], "team_name": row["team_name"], "models": {}}
        )
        entry["models"][row["model_version"]] = {
            "player_name": row["player_name"],
            "predicted_probability": row["predicted_probability"],
        }
    model_comparison_rows = sorted(model_comparison.values(), key=lambda r: r["pick_number"])

    summary = {
        "year": year,
        "prospects_loaded": prospects_loaded,
        "prospect_source": describe_prospect_source(get_prospect_sources(conn, year)),
        "picks_loaded": picks_loaded,
        "predictions_loaded": predictions_loaded,
        "top_ranked_available": top_available_rows[0]["full_name"] if top_available_rows else "N/A",
        "picks_made": picks_made,
    }

    return {
        "summary": summary,
        "top_250": top_250,
        "best_available": best_available,
        "picks": picks,
        "predictions": predictions,
        "fallers": fallers,
        "on_the_clock": on_the_clock,
        "draft_order": draft_order,
        "positions": positions,
        "teams": teams,
        "models": models,
        "model_comparison": model_comparison_rows,
    }


def fetch_latest_picks(conn: sqlite3.Connection, year: int, limit: int = 20) -> list[dict]:
    """Bounded, ascending-pick-number list of the most recent actual picks,
    for the in-browser polling notification - the client only needs to
    diff against picks newer than its own last-seen pick number, so this
    intentionally doesn't return the full draft history."""
    rows = conn.execute(
        """
        SELECT pick_number, round_label, team_name, player_name, player_position, school_name
        FROM actual_picks
        WHERE draft_year = ?
        ORDER BY pick_number DESC
        LIMIT ?
        """,
        (year, limit),
    ).fetchall()
    picks = [dict(r) for r in rows]
    picks.reverse()
    for p in picks:
        p["title"] = format_pick_title(p)
        p["summary"] = format_pick_summary(p)
        p["team_logo"] = team_logo_html(p["team_name"])
        p["team_logo_url"] = team_logo_url(p["team_name"])
    return picks


def fetch_team_pool_data(conn: sqlite3.Connection, year: int) -> list[dict]:
    """Each team's bonus-pool total (summed from draft_slots.pick_value,
    which the MLB Stats API already reports as a real dollar slot value for
    every round-1-10 pick and exactly 0 for round 11+ - confirmed against
    the live API, matches the real bonus-pool rules exactly) versus what's
    been used so far. A round-1-10 pick's full bonus counts against the
    pool; a round-11+ pick (no slot_value) only counts for whatever it pays
    above POST_10TH_ROUND_BONUS_POOL_THRESHOLD - see that constant's
    comment. No separate static bonus-pool file needed."""
    rows = conn.execute(
        """
        SELECT s.team_name,
               SUM(s.pick_value) AS pool_total,
               COALESCE((
                   SELECT SUM(
                       CASE WHEN a.slot_value > 0 THEN a.bonus_amount
                            ELSE MAX(a.bonus_amount - ?, 0)
                       END
                   ) FROM actual_picks a
                   WHERE a.team_name = s.team_name AND a.draft_year = s.draft_year
               ), 0) AS pool_used
        FROM draft_slots s
        WHERE s.draft_year = ? AND s.pick_value > 0
        GROUP BY s.team_name
        ORDER BY (pool_total - pool_used) ASC
        """,
        (POST_10TH_ROUND_BONUS_POOL_THRESHOLD, year),
    ).fetchall()
    result = []
    for r in rows:
        row = dict(r)
        total = row["pool_total"] or 0
        used = row["pool_used"] or 0
        row["pool_remaining"] = total - used
        row["pct_used"] = (used / total) if total else 0
        result.append(row)
    return result


def row_attrs(*, status=None, position=None, school=None, team=None, model=None) -> str:
    attrs = []
    if status:
        attrs.append(f'data-status="{esc(slugify(status))}"')
    if position:
        attrs.append(f'data-position="{esc(slugify(position))}"')
    if school:
        attrs.append(f'data-school="{esc(slugify(school))}"')
    if team:
        attrs.append(f'data-team="{esc(slugify(team))}"')
    if model:
        attrs.append(f'data-model="{esc(slugify(model))}"')
    return " ".join(attrs)


HEADER_TOOLTIPS = {
    "Win Prob.": "Modeled probability this player goes at this pick. See the Model/Models column for which "
    "prediction model produced it — magnitudes aren't directly comparable across models.",
    "Mock Prob.": "Share of real published mock drafts that projected this player to this team — actual "
    "analyst consensus, not a formula.",
}


def filterable_table(table_id, title, headers, rows, cell_keys, *, count_label="rows", empty_text="No matching rows.", default_status=None):
    head = "".join(
        f'<th title="{esc(HEADER_TOOLTIPS[h])}">{esc(h)}</th>' if h in HEADER_TOOLTIPS else f"<th>{esc(h)}</th>"
        for h in headers
    )
    body_rows = []
    for row in rows:
        if "is_drafted" in row:
            status = "Drafted" if row.get("is_drafted") else "Undrafted"
        else:
            status = default_status
        school = school_type(row["school_class"]) if "school_class" in row else None
        team = row.get("draft_team_name") or row.get("team_name")
        position = row.get("position_name")
        model = row.get("model_version")
        attrs = row_attrs(status=status, position=position, school=school, team=team, model=model)
        cells = "".join(
            f'<td data-label="{esc(h)}">'
            f'{team_logo_html(row.get(k, "")) if h in TEAM_LOGO_HEADERS else ""}'
            f'{signability_tag_html(row) if h in TAG_HEADERS else signability_badge_html(row) if h in SIGNABILITY_HEADERS else esc(row.get(k, ""))}'
            f'{prospect_info_button_html(row) if h in PLAYER_INFO_HEADERS else ""}'
            f'</td>'
            for h, k in zip(headers, cell_keys)
        )
        body_rows.append(f"<tr class=\"frow\" {attrs}>{cells}</tr>")

    return f"""
    <section class="panel" id="{table_id}">
      <div class="panel-header">
        <h2>{esc(title)}</h2>
        <span class="badge">{len(rows)} {esc(count_label)}</span>
      </div>
      <div class="table-wrap" data-tablewrap>
        <table>
          <thead><tr>{head}</tr></thead>
          <tbody>{''.join(body_rows)}</tbody>
        </table>
        <p class="table-empty">{esc(empty_text)}</p>
      </div>
    </section>
    """


def render_on_the_clock(otc):
    if not otc:
        return """
        <section class="panel highlight" id="on-the-clock">
          <div class="panel-header"><h2>On the Clock</h2></div>
          <p class="muted">No upcoming pick found for this draft year yet.</p>
        </section>
        """
    candidate_rows = "".join(
        f"""<tr>
              <td data-label="Player">{esc(c.get('player_name'))}</td>
              <td data-label="Position">{esc(c.get('position_name'))}</td>
              <td data-label="School">{esc(c.get('school_name'))}</td>
              <td data-label="Win Prob." title="{esc(HEADER_TOOLTIPS['Win Prob.'])}">{esc(c.get('predicted_probability'))}</td>
              <td data-label="Models">{'<span class="badge badge-accent">Agree</span>' if c.get('model_count', 1) > 1 else ''}</td>
            </tr>"""
        for c in otc["candidates"]
    )
    if not candidate_rows:
        candidate_rows = '<tr><td colspan="5" class="muted">No predictions generated for this pick yet.</td></tr>'

    return f"""
    <section class="panel highlight" id="on-the-clock">
      <div class="panel-header">
        <h2>On the Clock</h2>
        <span class="badge badge-accent">Pick #{esc(otc['pick_number'])}</span>
      </div>
      <p class="on-the-clock-team">{team_logo_html(otc['team_name'])}{esc(otc['team_name'])} <span class="muted">&middot; {esc(otc.get('round_label') or '')}{f" &middot; Slot value: {esc(format_usd(otc['pick_value']))}" if otc.get('pick_value') else ""}</span></p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Player</th><th>Position</th><th>School</th><th title="{esc(HEADER_TOOLTIPS['Win Prob.'])}">Win Prob.</th><th>Models</th></tr></thead>
          <tbody>{candidate_rows}</tbody>
        </table>
      </div>
    </section>
    """


def render_draft_order(groups, on_the_clock_pick_number, teams):
    if not groups:
        return """
        <section class="panel" id="draft-order">
          <div class="panel-header"><h2>Draft Order</h2></div>
          <p class="muted">No draft order loaded yet for this draft year.</p>
        </section>
        """

    default_label = groups[0]["round_label"]
    if on_the_clock_pick_number is not None:
        for group in groups:
            if any(p["pick_number"] == on_the_clock_pick_number for p in group["picks"]):
                default_label = group["round_label"]
                break

    tab_buttons = []
    round_panels = []
    for group in groups:
        slug = f"round-{slugify(group['round_label'])}"
        is_active = group["round_label"] == default_label
        tab_buttons.append(
            f'<button type="button" class="round-tab{" active" if is_active else ""}" '
            f'data-target="{slug}" onclick="showRound(this)" title="{esc(group["round_name"])}">'
            f'{esc(group["round_label"])}</button>'
        )
        pick_rows = "".join(
            f"""<tr class="{'otc-row' if p['pick_number'] == on_the_clock_pick_number else ''}" data-team="{esc(slugify(p['team_name']))}">
                  <td data-label="Pick">{esc(p['pick_number'])}</td>
                  <td data-label="Team">{team_logo_html(p['team_name'])}{esc(p['team_name'])}</td>
                  <td data-label="Player">{esc(p.get('player_name')) or ('<span class="badge badge-accent">On the Clock</span>' if p['pick_number'] == on_the_clock_pick_number else '<span class="muted">&mdash;</span>')}{prospect_info_button_html(p)}</td>
                  <td data-label="Position">{esc(p.get('position_name')) or ''}</td>
                  <td data-label="Slot Value">{esc(format_usd(p.get('pick_value')))}</td>
                </tr>"""
            for p in group["picks"]
        )
        round_panels.append(
            f"""
            <div class="round-panel" id="{slug}"{'' if is_active else ' style="display:none"'}>
              <h3 class="round-heading">{esc(group['round_name'])}</h3>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Pick</th><th>Team</th><th>Player</th><th>Position</th><th>Slot Value</th></tr></thead>
                  <tbody>{pick_rows}</tbody>
                </table>
              </div>
            </div>
            """
        )

    team_options = "".join(
        f'<option value="{esc(slugify(t))}">{esc(t)}</option>' for t in teams
    )

    total_picks = sum(len(group["picks"]) for group in groups)
    return f"""
    <section class="panel" id="draft-order">
      <div class="panel-header">
        <h2>Draft Order</h2>
        <div class="draft-order-controls">
          <select id="draft-order-team-filter" class="draft-order-team-select" onchange="filterDraftOrderByTeam()">
            <option value="">All teams</option>
            {team_options}
          </select>
          <span class="badge">{total_picks} picks</span>
        </div>
      </div>
      <div class="round-tabs" id="round-tabs">{''.join(tab_buttons)}</div>
      {''.join(round_panels)}
    </section>
    """


POOL_WARNING_THRESHOLD = 0.95


def format_usd(value) -> str:
    if value is None:
        return ""
    return f"${value:,.0f}"


def render_pool_tracker(rows):
    if not rows:
        return """
        <section class="panel" id="pool-tracker">
          <div class="panel-header"><h2>Bonus Pool Tracker</h2></div>
          <p class="muted">No draft order loaded yet — run sync-draft-order-api.</p>
        </section>
        """

    team_rows = []
    for row in rows:
        pct = min(row["pct_used"], 1.0)
        warning = row["pct_used"] >= POOL_WARNING_THRESHOLD
        team_rows.append(
            f"""
            <div class="pool-row" data-team="{esc(slugify(row['team_name']))}">
              <div class="pool-row-header">
                {team_logo_html(row['team_name'])}
                <span class="pool-team-name">{esc(row['team_name'])}</span>
                <span class="pool-amounts">{format_usd(row['pool_used'])} / {format_usd(row['pool_total'])}
                  <span class="muted">({row['pct_used']:.0%} used)</span></span>
              </div>
              <div class="pool-bar-track">
                <div class="pool-bar-fill{' pool-bar-warning' if warning else ''}" style="width:{pct * 100:.1f}%"></div>
              </div>
            </div>
            """
        )

    return f"""
    <section class="panel" id="pool-tracker">
      <div class="panel-header">
        <h2>Bonus Pool Tracker</h2>
        <span class="badge">{len(rows)} teams</span>
      </div>
      <div class="pool-tracker-list">{''.join(team_rows)}</div>
    </section>
    """


def render_filter_bar(positions, teams, models):
    position_options = "".join(
        f'<option value="{esc(slugify(p))}">{esc(p)}</option>' for p in positions
    )
    team_options = "".join(
        f'<option value="{esc(slugify(t))}">{esc(t)}</option>' for t in teams
    )
    model_options = "".join(
        f'<option value="{esc(slugify(m))}">{esc(m)}</option>' for m in models
    )
    return f"""
    <div class="filter-bar" id="filter-bar">
      <div class="filter-group filter-group-search">
        <label for="f-search">Search</label>
        <input id="f-search" type="text" placeholder="Search player, school, team&hellip;" oninput="applyFilters()">
      </div>
      <button type="button" class="btn-ghost filter-toggle-btn" onclick="toggleFilterBar()" aria-label="Show more filters">
        Filters <span id="filter-toggle-icon">&#9662;</span>
      </button>
      <div class="filter-group">
        <label for="f-status">Status</label>
        <select id="f-status" onchange="applyFilters()">
          <option value="">All</option>
          <option value="undrafted">Undrafted</option>
          <option value="drafted">Drafted</option>
        </select>
      </div>
      <div class="filter-group">
        <label for="f-position">Position</label>
        <select id="f-position" onchange="applyFilters()">
          <option value="">All positions</option>
          {position_options}
        </select>
      </div>
      <div class="filter-group">
        <label for="f-school">School Type</label>
        <select id="f-school" onchange="applyFilters()">
          <option value="">All schools</option>
          <option value="high-school">High School</option>
          <option value="college">College</option>
          <option value="junior-college">Junior College</option>
        </select>
      </div>
      <div class="filter-group">
        <label for="f-team">Team</label>
        <select id="f-team" onchange="applyFilters()">
          <option value="">All teams</option>
          {team_options}
        </select>
      </div>
      <div class="filter-group">
        <label for="f-model">Model</label>
        <select id="f-model" onchange="applyFilters()">
          <option value="">All models</option>
          {model_options}
        </select>
      </div>
      <button type="button" class="btn-ghost filter-reset-btn" onclick="resetFilters()">Reset</button>
    </div>
    """


def render_model_comparison(rows, models):
    if not rows:
        return """
        <section class="panel" id="model-comparison">
          <div class="panel-header"><h2>Model Comparison</h2></div>
          <p class="muted">No predictions generated yet — run generate-predictions and/or seed-mock-consensus.</p>
        </section>
        """

    model_headers = "".join(f"<th>{esc(m)}</th>" for m in models)
    body_rows = []
    for row in rows:
        cells = []
        picks_for_row = [row["models"].get(m) for m in models]
        distinct_players = {p["player_name"] for p in picks_for_row if p}
        agree = len(distinct_players) == 1 and len(picks_for_row) == len([p for p in picks_for_row if p]) and len(models) > 1
        for m in models:
            pick = row["models"].get(m)
            if pick:
                cells.append(f"<td data-label=\"{esc(m)}\">{esc(pick['player_name'])} <span class=\"muted\">({esc(pick['predicted_probability'])})</span></td>")
            else:
                cells.append(f'<td data-label="{esc(m)}" class="muted">&mdash;</td>')
        agreement_cell = (
            '<td data-label="Agreement"><span class="badge badge-accent">Agree</span></td>'
            if agree
            else '<td data-label="Agreement"></td>'
        )
        body_rows.append(
            f"<tr><td data-label=\"Pick\">{esc(row['pick_number'])}</td>"
            f"<td data-label=\"Team\">{team_logo_html(row['team_name'])}{esc(row['team_name'])}</td>{''.join(cells)}{agreement_cell}</tr>"
        )

    return f"""
    <section class="panel" id="model-comparison">
      <div class="panel-header">
        <h2>Model Comparison</h2>
        <span class="badge">{len(rows)} picks</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Pick</th><th>Team</th>{model_headers}<th>Agreement</th></tr></thead>
          <tbody>{''.join(body_rows)}</tbody>
        </table>
      </div>
    </section>
    """


STYLE = """
:root, :root[data-theme="dark"] {
  --bg: #0b1120;
  --bg-elevated: #131c31;
  --bg-card: #161f38;
  --border: #253352;
  --text: #e7ecf7;
  --text-muted: #93a1c2;
  --accent: #3b82f6;
  --accent-soft: rgba(59, 130, 246, 0.15);
  --success: #22c55e;
  --success-soft: rgba(34, 197, 94, 0.15);
  --warning: #f59e0b;
  --warning-soft: rgba(245, 158, 11, 0.15);
  --radius: 12px;
  --shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
  --body-gradient-center: #101a30;
  --chrome-bg: rgba(11, 17, 32, 0.92);
  --chrome-bg-soft: rgba(11, 17, 32, 0.85);
  --row-stripe: rgba(255, 255, 255, 0.015);
}

:root[data-theme="light"] {
  --bg: #eef1f8;
  --bg-elevated: #ffffff;
  --bg-card: #ffffff;
  --border: #d9e0ee;
  --text: #101828;
  --text-muted: #5b6578;
  --accent: #2563eb;
  --accent-soft: rgba(37, 99, 235, 0.12);
  --success: #16a34a;
  --success-soft: rgba(22, 163, 74, 0.12);
  --warning: #d97706;
  --warning-soft: rgba(217, 119, 6, 0.12);
  --radius: 12px;
  --shadow: 0 8px 24px rgba(16, 24, 40, 0.08);
  --body-gradient-center: #e3e9f7;
  --chrome-bg: rgba(255, 255, 255, 0.9);
  --chrome-bg-soft: rgba(255, 255, 255, 0.8);
  --row-stripe: rgba(16, 24, 40, 0.02);
}

* { box-sizing: border-box; }

html {
  /* iOS Safari reveals this color during scroll-bounce and behind its own
     collapsing toolbar chrome - without it those areas stay white regardless
     of the active theme, even though body itself is styled correctly. */
  background: var(--bg);
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  margin: 0;
  background: radial-gradient(circle at top, var(--body-gradient-center) 0%, var(--bg) 55%);
  color: var(--text);
  min-height: 100vh;
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  padding: 18px 32px;
  /* With viewport-fit=cover, the page now renders under the iOS notch/
     status bar instead of leaving that strip as uncolored browser chrome -
     this keeps the topbar's dark background filling it instead of the
     header content sliding up underneath the clock/Dynamic Island. */
  padding-top: max(18px, env(safe-area-inset-top));
  background: var(--chrome-bg);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--border);
}

.brand { display: flex; align-items: baseline; gap: 10px; }
.brand h1 { font-size: 19px; margin: 0; letter-spacing: 0.2px; }
.brand .build-version {
  font-size: 11px;
  font-weight: 400;
  font-family: monospace;
  color: var(--text-muted);
  vertical-align: middle;
}
.brand .year-pill {
  font-size: 12px;
  color: var(--text-muted);
  border: 1px solid var(--border);
  padding: 2px 10px;
  border-radius: 999px;
}

.topbar-controls { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }

.year-nav { display: flex; gap: 6px; background: var(--bg-elevated); padding: 4px; border-radius: 999px; border: 1px solid var(--border); }
.year-nav a {
  display: inline-block;
  padding: 6px 14px;
  border-radius: 999px;
  text-decoration: none;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 600;
}
.year-nav a.active { background: var(--accent); color: white; }

.refresh-toggle { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-muted); }
.refresh-toggle input { accent-color: var(--accent); }

.subnav {
  position: sticky;
  top: 62px;
  z-index: 19;
  display: flex;
  gap: 4px;
  padding: 10px 32px;
  background: var(--chrome-bg-soft);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--border);
  overflow-x: auto;
}
.subnav a {
  color: var(--text-muted);
  text-decoration: none;
  font-size: 13px;
  font-weight: 600;
  padding: 6px 12px;
  border-radius: 8px;
  white-space: nowrap;
}
.subnav a:hover { color: var(--text); background: var(--bg-elevated); }

main { padding: 28px 32px 60px; max-width: 1440px; margin: 0 auto; }

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 28px;
}
.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  padding: 18px 20px;
  border-radius: var(--radius);
  box-shadow: var(--shadow);
}
.card h3 {
  margin: 0 0 8px;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  color: var(--text-muted);
  font-weight: 600;
}
.card .value { font-size: 26px; font-weight: 700; color: var(--text); }

.schedule-banner {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 16px 20px;
  margin-bottom: 20px;
}
.schedule-banner-header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.schedule-banner-title { font-weight: 700; font-size: 15px; }
.schedule-banner-location { color: var(--text-muted); font-size: 13px; }
.schedule-banner-status { margin-top: 8px; font-size: 14px; }
.schedule-milestones { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
.schedule-milestone {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 12px;
  color: var(--text-muted);
  min-width: 190px;
  flex: 1 1 190px;
}
.schedule-milestone.active { border-color: var(--accent); background: var(--accent-soft); color: var(--text); }
.schedule-milestone-time { font-weight: 600; color: var(--text); display: block; margin-bottom: 2px; }

.filter-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 14px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 18px;
  margin-bottom: 28px;
  position: sticky;
  top: 116px;
  z-index: 15;
  box-shadow: var(--shadow);
}
.filter-toggle-btn { display: none; }

.filter-group { display: flex; flex-direction: column; gap: 6px; }
.filter-group label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); }
.filter-group input, .filter-group select {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 8px 10px;
  border-radius: 8px;
  font-size: 13px;
  min-width: 150px;
}
.filter-group input:focus, .filter-group select:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
.btn-ghost {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
  padding: 9px 14px;
  border-radius: 8px;
  font-size: 13px;
  cursor: pointer;
  height: 37px;
}
.btn-ghost:hover { color: var(--text); border-color: var(--accent); }

.theme-toggle-btn { padding: 9px 12px; font-size: 15px; line-height: 1; }

.panel {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px 22px;
  margin-bottom: 24px;
  box-shadow: var(--shadow);
  scroll-margin-top: 190px;
}
.panel.highlight { border-color: rgba(59, 130, 246, 0.4); }
.panel-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; flex-wrap: wrap; gap: 8px; }
.panel-header h2 { margin: 0; font-size: 17px; }

.draft-order-controls { display: flex; align-items: center; gap: 10px; }
.draft-order-team-select {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 7px 10px;
  border-radius: 8px;
  font-size: 13px;
}
.draft-order-team-select:focus { outline: 2px solid var(--accent); outline-offset: 1px; }

.badge {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  padding: 3px 10px;
  border-radius: 999px;
}
.badge-accent { color: white; background: var(--accent); border-color: var(--accent); }
.badge-warning { color: var(--warning); background: var(--warning-soft); border-color: var(--warning); }

.card .badge { display: inline-block; margin-top: 8px; }

.on-the-clock-team { font-size: 20px; font-weight: 700; margin: 0 0 14px; }

.team-logo { width: 18px; height: 18px; vertical-align: -4px; margin-right: 6px; }
.team-logo-light { display: none; }
:root[data-theme="light"] .team-logo-dark { display: none; }
:root[data-theme="light"] .team-logo-light { display: inline; }

.toast-container {
  position: fixed;
  top: 80px;
  right: 20px;
  z-index: 200;
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 320px;
}
.pick-toast {
  background: var(--bg-card);
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 14px 34px 14px 16px;
  position: relative;
  animation: pick-toast-in 0.25s ease-out;
}
.pick-toast-title { font-weight: 700; font-size: 13px; color: var(--accent); margin-bottom: 4px; }
.pick-toast-body { font-size: 13.5px; color: var(--text); }
.pick-toast-close {
  position: absolute;
  top: 6px;
  right: 8px;
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 18px;
  cursor: pointer;
  line-height: 1;
  padding: 4px;
}
.pick-toast-close:hover { color: var(--text); }
@keyframes pick-toast-in {
  from { transform: translateX(24px); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}

@media (max-width: 720px) {
  .toast-container { left: 16px; right: 16px; max-width: none; }
}

.prospect-info-btn {
  background: none;
  border: none;
  color: var(--accent);
  font-size: 15px;
  cursor: pointer;
  padding: 0 0 0 4px;
  line-height: 1;
  vertical-align: -2px;
}
.prospect-info-btn:hover { color: var(--text); }

.prospect-card-overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  z-index: 300;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.prospect-card-overlay.visible { display: flex; }
.prospect-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 24px 26px;
  max-width: 480px;
  width: 100%;
  max-height: 80vh;
  overflow-y: auto;
  position: relative;
}
.prospect-card h3 { margin: 0 0 16px; font-size: 19px; padding-right: 24px; }
.prospect-card-meta { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.prospect-card-headshot {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  object-fit: cover;
  border: 1px solid var(--border);
  flex-shrink: 0;
}
.prospect-card-meta-line { margin: 0; font-size: 13px; color: var(--text-muted); line-height: 1.4; }
.prospect-card-section { margin-bottom: 16px; }
.prospect-card-section:last-child { margin-bottom: 0; }
.prospect-card-section h4 {
  margin: 0 0 6px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
  font-weight: 600;
}
.prospect-card-section p { margin: 0; font-size: 14px; line-height: 1.5; color: var(--text); white-space: pre-wrap; }
.prospect-card-section p a { color: var(--accent); text-decoration: none; font-weight: 600; }
.prospect-card-section p a:hover { text-decoration: underline; }
.prospect-card-section p:empty::before { content: "Not available."; color: var(--text-muted); font-style: italic; }
.prospect-card-factors { margin: 8px 0 0; padding-left: 18px; font-size: 13px; color: var(--text-muted); }
.prospect-card-factors li { margin-bottom: 2px; }
.prospect-card-close {
  position: absolute;
  top: 14px;
  right: 16px;
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 20px;
  cursor: pointer;
  line-height: 1;
  padding: 4px;
}
.prospect-card-close:hover { color: var(--text); }

.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
th, td { padding: 10px 12px; text-align: left; white-space: nowrap; }
thead th {
  position: sticky;
  top: 0;
  background: var(--bg-elevated);
  color: var(--text-muted);
  text-transform: uppercase;
  font-size: 11px;
  letter-spacing: 0.5px;
  border-bottom: 1px solid var(--border);
}
tbody tr { border-bottom: 1px solid var(--border); }
tbody tr:hover { background: rgba(59, 130, 246, 0.08); }
tbody tr:nth-child(even) { background: var(--row-stripe); }

.table-empty { display: none; padding: 18px 4px; color: var(--text-muted); font-size: 13px; }

.round-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border);
}
.round-tab {
  font-family: inherit;
  background: var(--bg-elevated);
  color: var(--text-muted);
  border: 1px solid var(--border);
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
}
.round-tab:hover { color: var(--text); border-color: var(--accent); }
.round-tab.active { background: var(--accent); color: white; border-color: var(--accent); }

.round-heading {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
tr.otc-row { background: var(--accent-soft); }
tr.otc-row:hover { background: var(--accent-soft); }

.pool-tracker-list { display: flex; flex-direction: column; gap: 14px; }
.pool-row-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 13.5px;
}
.pool-team-name { font-weight: 600; }
.pool-amounts { margin-left: auto; color: var(--text); font-variant-numeric: tabular-nums; }
.pool-bar-track {
  height: 10px;
  border-radius: 999px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  overflow: hidden;
}
.pool-bar-fill { height: 100%; background: var(--accent); border-radius: 999px; }
.pool-bar-fill.pool-bar-warning { background: var(--warning); }

.muted { color: var(--text-muted); }

footer { text-align: center; padding: 20px; color: var(--text-muted); font-size: 12px; }

@media (max-width: 720px) {
  .topbar, .subnav, main { padding-left: 16px; padding-right: 16px; }

  /* Keep the filter bar sticky like on desktop, but collapsed to just the
     search box + a toggle by default - with 6 filter groups it would
     otherwise stack into several rows and, combined with the sticky
     topbar/subnav above it, eat most of a phone screen before any real
     content is visible. */
  .filter-toggle-btn { display: inline-flex; align-items: center; gap: 6px; }
  .filter-bar:not(.expanded) .filter-group:not(.filter-group-search),
  .filter-bar:not(.expanded) .filter-reset-btn {
    display: none;
  }

  /* Wide tables would otherwise need horizontal scrolling to see every
     column - stack each row into a label/value card instead so nothing
     is hidden or requires swiping. */
  .table-wrap { overflow-x: visible; }
  .table-wrap table thead { display: none; }
  .table-wrap table, .table-wrap table tbody, .table-wrap table tr, .table-wrap table td {
    display: block;
    width: 100%;
  }
  .table-wrap table tr {
    margin-bottom: 10px;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 4px 12px;
    background: var(--bg-elevated);
  }
  .table-wrap table tr:last-child { margin-bottom: 0; }
  .table-wrap table td {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
    white-space: normal;
    text-align: right;
  }
  .table-wrap table td:last-child { border-bottom: none; }
  .table-wrap table td[data-label]::before {
    content: attr(data-label);
    font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    font-size: 10.5px;
    letter-spacing: 0.4px;
    text-align: left;
    flex-shrink: 0;
  }
  .table-wrap table td[colspan] {
    display: block;
    text-align: left;
  }
}
"""

SCRIPT = """
function applyFilters() {
  const status = document.getElementById('f-status').value;
  const position = document.getElementById('f-position').value;
  const school = document.getElementById('f-school').value;
  const team = document.getElementById('f-team').value;
  const model = document.getElementById('f-model').value;
  const search = document.getElementById('f-search').value.trim().toLowerCase();

  document.querySelectorAll('tr.frow').forEach(function (row) {
    var visible = true;
    if (status && row.dataset.status && row.dataset.status !== status) visible = false;
    if (position && row.dataset.position && row.dataset.position !== position) visible = false;
    if (school && row.dataset.school && row.dataset.school !== school) visible = false;
    if (team && row.dataset.team && row.dataset.team !== team) visible = false;
    if (model && row.dataset.model && row.dataset.model !== model) visible = false;
    if (search && !row.textContent.toLowerCase().includes(search)) visible = false;
    row.style.display = visible ? '' : 'none';
  });
  updateEmptyStates();
}

function updateEmptyStates() {
  document.querySelectorAll('[data-tablewrap]').forEach(function (wrap) {
    const rows = wrap.querySelectorAll('tbody tr.frow');
    const visibleCount = Array.prototype.filter.call(rows, function (r) { return r.style.display !== 'none'; }).length;
    const empty = wrap.querySelector('.table-empty');
    if (empty) empty.style.display = (visibleCount === 0) ? 'block' : 'none';
  });
}

function resetFilters() {
  document.getElementById('f-search').value = '';
  document.getElementById('f-status').value = '';
  document.getElementById('f-position').value = '';
  document.getElementById('f-school').value = '';
  document.getElementById('f-team').value = '';
  document.getElementById('f-model').value = '';
  applyFilters();
}

function toggleFilterBar() {
  const bar = document.getElementById('filter-bar');
  const expanded = bar.classList.toggle('expanded');
  const icon = document.getElementById('filter-toggle-icon');
  if (icon) icon.innerHTML = expanded ? '&#9652;' : '&#9662;';
}

// Floating scouting-report card: a single shared overlay/card pair, filled
// in from whichever info button was clicked (data-name/data-blurb/
// data-scouting attributes) rather than rendering one card per row - keeps
// the summary tables from having to carry a modal's worth of markup per
// player just to show text most rows won't ever open.
function showProspectCard(btn) {
  document.getElementById('prospect-card-name').textContent = btn.dataset.name;
  document.getElementById('prospect-card-blurb').textContent = btn.dataset.blurb;

  const headshotEl = document.getElementById('prospect-card-headshot');
  if (btn.dataset.headshot) {
    headshotEl.src = btn.dataset.headshot;
    headshotEl.alt = btn.dataset.name;
    headshotEl.style.display = '';
  } else {
    headshotEl.style.display = 'none';
    headshotEl.removeAttribute('src');
  }
  const metaParts = [btn.dataset.position, btn.dataset.school, btn.dataset.bt, btn.dataset.hometown, btn.dataset.class]
    .filter(function (p) { return p; });
  document.getElementById('prospect-card-meta-line').textContent = metaParts.join(' · ');

  const bonusSection = document.getElementById('prospect-card-bonus-section');
  if (btn.dataset.bonus) {
    let text = 'Signed for ' + btn.dataset.bonus;
    if (btn.dataset.slot) text += ' (slot value ' + btn.dataset.slot + ')';
    if (btn.dataset.overUnder) text += ' — ' + btn.dataset.overUnder + ' slot';
    document.getElementById('prospect-card-bonus').textContent = text;
    bonusSection.style.display = '';
  } else {
    bonusSection.style.display = 'none';
  }

  const signabilitySection = document.getElementById('prospect-card-signability-section');
  const factorsList = document.getElementById('prospect-card-signability-factors');
  factorsList.textContent = '';
  if (btn.dataset.signabilityTier) {
    document.getElementById('prospect-card-signability-summary').textContent =
      btn.dataset.signabilityTier + ' (' + btn.dataset.signabilityScore + '/100)';
    (btn.dataset.signabilityFactors || '').split('|').filter(function (f) { return f; }).forEach(function (factor) {
      const li = document.createElement('li');
      li.textContent = factor;
      factorsList.appendChild(li);
    });
    signabilitySection.style.display = '';
  } else {
    signabilitySection.style.display = 'none';
  }

  // MLB's "scoutingReport" field is consistently a link to a scouting video
  // rather than free text - render it as a clickable link when it looks
  // like a URL, built via DOM APIs (not innerHTML) so there's no injection
  // risk even though this data comes from our own backend.
  const scoutingEl = document.getElementById('prospect-card-scouting');
  const scouting = btn.dataset.scouting || '';
  scoutingEl.textContent = '';
  if (/^https?:/.test(scouting)) {
    const link = document.createElement('a');
    link.href = scouting;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = 'Watch scouting video ↗';
    scoutingEl.appendChild(link);
  } else {
    scoutingEl.textContent = scouting;
  }

  document.getElementById('prospect-card-overlay').classList.add('visible');
}

function hideProspectCard(event) {
  if (event && event.target !== event.currentTarget) return;
  document.getElementById('prospect-card-overlay').classList.remove('visible');
}

document.addEventListener('keydown', function (event) {
  if (event.key === 'Escape') hideProspectCard();
});

function showRound(btn) {
  const target = btn.getAttribute('data-target');
  document.querySelectorAll('.round-tab').forEach(function (t) {
    t.classList.toggle('active', t === btn);
  });
  document.querySelectorAll('.round-panel').forEach(function (p) {
    p.style.display = (p.id === target) ? '' : 'none';
  });
}

// Draft Order has two mutually-exclusive browsing modes: round-first (the
// tabs, one round visible at a time) and team-first (this filter, every
// round visible but narrowed to one team's picks). All 613 picks are
// already in the DOM across the round panels, so switching modes is just
// a visibility toggle - no extra data fetch needed.
function filterDraftOrderByTeam() {
  const team = document.getElementById('draft-order-team-filter').value;
  const tabsBar = document.getElementById('round-tabs');
  const panels = document.querySelectorAll('.round-panel');

  if (!team) {
    if (tabsBar) tabsBar.style.display = '';
    const activeTab = document.querySelector('.round-tab.active');
    const activeTarget = activeTab ? activeTab.getAttribute('data-target') : null;
    panels.forEach(function (p) {
      p.querySelectorAll('tbody tr').forEach(function (row) { row.style.display = ''; });
      p.style.display = (p.id === activeTarget) ? '' : 'none';
    });
    return;
  }

  if (tabsBar) tabsBar.style.display = 'none';
  panels.forEach(function (p) {
    let anyVisible = false;
    p.querySelectorAll('tbody tr').forEach(function (row) {
      const match = row.dataset.team === team;
      row.style.display = match ? '' : 'none';
      if (match) anyVisible = true;
    });
    p.style.display = anyVisible ? '' : 'none';
  });
}

function initAutoRefresh() {
  const toggle = document.getElementById('auto-refresh');
  const stored = localStorage.getItem('mlb_autorefresh') === '1';
  toggle.checked = stored;
  if (stored) scheduleRefresh();
  toggle.addEventListener('change', function () {
    localStorage.setItem('mlb_autorefresh', toggle.checked ? '1' : '0');
    if (toggle.checked) scheduleRefresh();
  });
}

function scheduleRefresh() {
  setTimeout(function () { window.location.reload(); }, 30000);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = current === 'light' ? 'dark' : 'light';
  // A cookie (not localStorage) so the server can render data-theme and
  // theme-color directly into the next response - iOS Safari's status-bar/
  // notch overlay only reflects theme-color's value from the initial HTML,
  // not later DOM mutations, so this has to be correct from the first byte
  // rather than patched in after the fact.
  document.cookie = 'mlb_theme=' + next + '; path=/; max-age=31536000; SameSite=Lax';
  window.location.reload();
}

function applyThemeUi(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.textContent = theme === 'light' ? '🌙' : '☀️';
  const meta = document.querySelector('meta[name="theme-color"]');
  const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  if (meta && bg) meta.setAttribute('content', bg);
}

// The topbar/subnav/filter-bar stack on top of each other via
// position:sticky - their offsets depend on the topbar's actual rendered
// height, which varies (it wraps to two lines on narrow screens). Measure
// it directly instead of hardcoding a breakpoint-specific pixel value, so
// the sub-nav links stay reachable while scrolling at any viewport width.
function syncStickyOffsets() {
  const topbar = document.querySelector('.topbar');
  const subnav = document.querySelector('.subnav');
  const filterBar = document.querySelector('.filter-bar');
  const toastContainer = document.getElementById('toast-container');
  const topbarH = topbar ? topbar.offsetHeight : 0;
  if (subnav) subnav.style.top = topbarH + 'px';
  const subnavH = subnav ? subnav.offsetHeight : 0;
  if (filterBar && getComputedStyle(filterBar).position === 'sticky') {
    filterBar.style.top = (topbarH + subnavH) + 'px';
  }
  // Toasts anchor top-right (not bottom) so they don't cover content at the
  // bottom of a phone screen - but they still need to clear the sticky
  // topbar/subnav stack, whose height varies once the topbar wraps.
  if (toastContainer) toastContainer.style.top = (topbarH + subnavH + 12) + 'px';
}

// In-browser pick notifications - polls the same actual_picks data the
// Telegram poller alerts on, so a user watching the dashboard finds out
// about a new pick without needing Telegram. Deliberately simple polling
// rather than a websocket/SSE push: picks happen minutes apart at
// fastest, so a 12s interval is plenty responsive without needing a
// persistent connection.
const DEFAULT_PICK_POLL_INTERVAL_MS = 12000;
// Mutable (not const): pollDraftMilestone() rescales this to match the real
// draft-day pick pace from draft_schedule.py once /api/draft-milestone loads.
let pickPollIntervalMs = DEFAULT_PICK_POLL_INTERVAL_MS;
let pickPollTimer = null;

function schedulePickPolling() {
  if (pickPollTimer) clearInterval(pickPollTimer);
  pickPollTimer = setInterval(pollLatestPicks, pickPollIntervalMs);
}

function currentDraftYear() {
  return new URLSearchParams(window.location.search).get('year') || '2026';
}

function lastSeenPickKey(year) {
  return 'mlb_last_seen_pick_' + year;
}

function getLastSeenPick(year) {
  return parseInt(localStorage.getItem(lastSeenPickKey(year)) || '0', 10);
}

function setLastSeenPick(year, pickNumber) {
  localStorage.setItem(lastSeenPickKey(year), String(pickNumber));
}

function showPickToast(pick) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = 'pick-toast';
  const title = document.createElement('div');
  title.className = 'pick-toast-title';
  title.textContent = pick.title;
  const body = document.createElement('div');
  body.className = 'pick-toast-body';
  body.innerHTML = pick.team_logo; // trusted, server-rendered <img> tags - same markup the dashboard tables use
  body.appendChild(document.createTextNode(pick.summary));
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'pick-toast-close';
  close.setAttribute('aria-label', 'Dismiss');
  close.innerHTML = '&times;';
  close.onclick = function () { toast.remove(); };
  toast.appendChild(close);
  toast.appendChild(title);
  toast.appendChild(body);
  container.appendChild(toast);
  setTimeout(function () { if (toast.parentElement) toast.remove(); }, 10000);
}

function maybeNativeNotify(pick) {
  if (localStorage.getItem('mlb_pick_alerts') !== '1') return;
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
  // document.hidden only tracks tab visibility (is this the active tab in a
  // non-minimized window) - it stays false even when the user has switched
  // OS focus away to another app (e.g. a terminal) while the dashboard tab
  // sits open in the background. Checking hasFocus() too means the native
  // notification only gets suppressed when the in-page toast is actually
  // guaranteed to be seen.
  if (!document.hidden && document.hasFocus()) return;
  // Native notifications can't render our two-variant light/dark <img> markup
  // in the body, but the Notification API does support a single icon image.
  new Notification(pick.title, { body: pick.summary, icon: pick.team_logo_url || undefined });
}

function pollLatestPicks() {
  const year = currentDraftYear();
  fetch('/api/latest-picks?year=' + encodeURIComponent(year), { cache: 'no-store' })
    .then(function (resp) { return resp.ok ? resp.json() : null; })
    .then(function (data) {
      if (!data || !data.picks.length) return;
      const lastSeen = getLastSeenPick(year);
      const newPicks = data.picks.filter(function (p) { return p.pick_number > lastSeen; });
      newPicks.forEach(function (p) {
        showPickToast(p);
        maybeNativeNotify(p);
      });
      const maxPick = Math.max.apply(null, data.picks.map(function (p) { return p.pick_number; }));
      setLastSeenPick(year, maxPick);
    })
    .catch(function () { /* transient network hiccup - next interval retries */ });
}

function initPickPolling() {
  const year = currentDraftYear();
  // Seed last-seen to whatever's already happened on first-ever visit so
  // opening the dashboard mid-draft doesn't immediately dump a toast for
  // every prior pick - only picks from this point forward should notify.
  fetch('/api/latest-picks?year=' + encodeURIComponent(year), { cache: 'no-store' })
    .then(function (resp) { return resp.ok ? resp.json() : null; })
    .then(function (data) {
      if (data && data.picks.length && getLastSeenPick(year) === 0) {
        const maxPick = Math.max.apply(null, data.picks.map(function (p) { return p.pick_number; }));
        setLastSeenPick(year, maxPick);
      }
    })
    .finally(function () {
      schedulePickPolling();
    });
}

function lastSeenMilestoneKey(year) {
  return 'mlb_last_seen_milestone_' + year;
}

function getLastSeenMilestone(year) {
  return localStorage.getItem(lastSeenMilestoneKey(year)) || '';
}

function setLastSeenMilestone(year, key) {
  localStorage.setItem(lastSeenMilestoneKey(year), key);
}

function renderScheduleStatus(data) {
  const el = document.getElementById('schedule-banner-status');
  if (!el) return;
  el.textContent = '';
  const badge = document.createElement('span');
  badge.className = 'badge' + (data.current ? ' badge-accent' : '');
  badge.textContent = data.current ? 'Live now' : (data.next ? 'Next up' : 'Complete');
  el.appendChild(badge);
  let text;
  if (data.current) {
    text = ' ' + data.current.label + ' · ' + data.current.picks_label + ' on ' + data.current.channels;
  } else if (data.next) {
    const start = new Date(data.next.start).toLocaleString('en-US', {
      weekday: 'short', hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York',
    });
    text = ' ' + data.next.label + ' starts ' + start + ' ET · ' + data.next.picks_label + ' on ' + data.next.channels;
  } else {
    text = ' All scheduled draft windows have concluded.';
  }
  el.appendChild(document.createTextNode(text));
}

function pollDraftMilestone() {
  const banner = document.getElementById('draft-schedule-banner');
  if (!banner) return;
  const year = currentDraftYear();
  fetch('/api/draft-milestone?year=' + encodeURIComponent(year), { cache: 'no-store' })
    .then(function (resp) { return resp.ok ? resp.json() : null; })
    .then(function (data) {
      if (!data) return;
      renderScheduleStatus(data);
      document.querySelectorAll('.schedule-milestone').forEach(function (el) {
        el.classList.toggle('active', !!data.current && el.dataset.milestoneKey === data.current.key);
      });
      if (data.poll_interval_seconds) {
        const ms = data.poll_interval_seconds * 1000;
        if (ms !== pickPollIntervalMs) {
          pickPollIntervalMs = ms;
          schedulePickPolling();
        }
      }
      const currentKey = data.current ? data.current.key : '';
      if (currentKey && currentKey !== getLastSeenMilestone(year)) {
        // Reuses the same "Pick alerts" opt-in/permission flow as per-pick
        // toasts (initPickAlertsToggle) rather than a second toggle - both
        // are "live draft alert" categories from the user's point of view.
        showPickToast({ title: 'Draft milestone starting', team_logo: '', summary: data.current.label + ' — ' + data.current.picks_label + ' on ' + data.current.channels });
        maybeNativeNotify({ title: data.current.label, summary: data.current.picks_label + ' on ' + data.current.channels, team_logo_url: undefined });
      }
      if (currentKey) setLastSeenMilestone(year, currentKey);
    })
    .catch(function () { /* transient network hiccup - next interval retries */ });
}

function initDraftScheduleBanner() {
  const banner = document.getElementById('draft-schedule-banner');
  if (!banner) return;
  const year = currentDraftYear();
  // Seed last-seen to whatever's active right now on first-ever visit, same
  // reasoning as initPickPolling: opening the dashboard mid-window shouldn't
  // immediately fire a "starting" alert for something already underway.
  fetch('/api/draft-milestone?year=' + encodeURIComponent(year), { cache: 'no-store' })
    .then(function (resp) { return resp.ok ? resp.json() : null; })
    .then(function (data) {
      if (!data) return;
      renderScheduleStatus(data);
      if (data.current && !getLastSeenMilestone(year)) {
        setLastSeenMilestone(year, data.current.key);
      }
      if (data.poll_interval_seconds) {
        pickPollIntervalMs = data.poll_interval_seconds * 1000;
      }
    })
    .finally(function () {
      setInterval(pollDraftMilestone, 20000);
    });
}

function initPickAlertsToggle() {
  const toggle = document.getElementById('pick-alerts-toggle');
  if (!toggle) return;
  const hasNotifications = typeof Notification !== 'undefined';
  toggle.checked = hasNotifications && localStorage.getItem('mlb_pick_alerts') === '1' && Notification.permission === 'granted';
  toggle.addEventListener('change', function () {
    if (!toggle.checked) {
      localStorage.setItem('mlb_pick_alerts', '0');
      return;
    }
    if (!hasNotifications) {
      toggle.checked = false;
      return;
    }
    Notification.requestPermission().then(function (permission) {
      const granted = permission === 'granted';
      toggle.checked = granted;
      localStorage.setItem('mlb_pick_alerts', granted ? '1' : '0');
    });
  });
}

// 2026 signing deadline: 5:00 PM ET on July 27 - ET is UTC-4 (EDT) in July.
const SIGNING_DEADLINE = new Date('2026-07-27T17:00:00-04:00');

function updateSigningDeadlineCountdown() {
  const el = document.getElementById('signing-deadline-countdown');
  if (!el) return;
  const remainingMs = SIGNING_DEADLINE - new Date();
  if (remainingMs <= 0) {
    el.textContent = 'Passed';
    return;
  }
  const totalMinutes = Math.floor(remainingMs / 60000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  el.textContent = days + 'd ' + hours + 'h ' + minutes + 'm';
}

document.addEventListener('DOMContentLoaded', function () {
  initAutoRefresh();
  updateEmptyStates();
  applyThemeUi(document.documentElement.getAttribute('data-theme') || 'dark');
  syncStickyOffsets();
  initDraftScheduleBanner();
  initPickPolling();
  initPickAlertsToggle();
  updateSigningDeadlineCountdown();
  setInterval(updateSigningDeadlineCountdown, 60000);
});
window.addEventListener('resize', syncStickyOffsets);
window.addEventListener('load', syncStickyOffsets);
"""


def app_factory(db_path: str):
    def app(environ, start_response):
        if environ.get("PATH_INFO") == "/favicon.ico":
            start_response(
                "200 OK",
                [("Content-Type", "image/x-icon"), ("Cache-Control", "public, max-age=86400")],
            )
            return [FAVICON_BYTES]

        init_db(db_path)
        year = get_selected_year(environ, default_year=2026)
        theme = get_selected_theme(environ)

        if environ.get("PATH_INFO") == "/api/latest-picks":
            conn = get_connection(db_path)
            picks = fetch_latest_picks(conn, year)
            conn.close()
            body = json.dumps({"year": year, "picks": picks}).encode("utf-8")
            start_response(
                "200 OK",
                [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")],
            )
            return [body]

        if environ.get("PATH_INFO") == "/api/draft-milestone":
            body = json.dumps(draft_milestone_payload(year)).encode("utf-8")
            start_response(
                "200 OK",
                [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")],
            )
            return [body]

        conn = get_connection(db_path)
        data = fetch_dashboard_data(conn, year)
        pool_data = fetch_team_pool_data(conn, year)
        conn.close()

        summary = data["summary"]
        schedule_banner = render_draft_schedule_banner(year)

        cards = f"""
        <div class="cards">
          <div class="card">
            <h3>Prospects Loaded</h3>
            <div class="value">{summary['prospects_loaded']}</div>
            <span class="badge{' badge-warning' if 'Mixed sources' in summary['prospect_source'] else ''}" title="Where the current prospect board data came from">{esc(summary['prospect_source'])}</span>
          </div>
          <div class="card"><h3>Actual Picks Loaded</h3><div class="value">{summary['picks_loaded']}</div></div>
          <div class="card"><h3>Predictions Loaded</h3><div class="value">{summary['predictions_loaded']}</div></div>
          <div class="card"><h3>Top Available</h3><div class="value">{esc(summary['top_ranked_available'])}</div></div>
          <div class="card">
            <h3>Signing Deadline</h3>
            <div class="value" id="signing-deadline-countdown">&mdash;</div>
          </div>
        </div>
        """

        filter_bar = render_filter_bar(data["positions"], data["teams"], data["models"])
        on_the_clock = render_on_the_clock(data["on_the_clock"])
        otc_pick_number = data["on_the_clock"]["pick_number"] if data["on_the_clock"] else None
        draft_order_panel = render_draft_order(data["draft_order"], otc_pick_number, data["teams"])
        pool_tracker_panel = render_pool_tracker(pool_data)
        model_comparison_panel = render_model_comparison(data["model_comparison"], data["models"])

        best_available_panel = filterable_table(
            "best-available",
            "Best Available",
            ["Rank", "Player", "Position", "School", "Age", "B/T", "Mock Team", "Mock Prob."],
            data["best_available"],
            ["rank", "full_name", "position_name", "school_name", "current_age", "bats_throws", "mock_team", "mock_probability"],
            count_label="prospects",
            default_status="Undrafted",
        )

        fallers_panel = filterable_table(
            "fallers",
            "Biggest Fallers",
            ["Rank", "Player", "Position", "School", "Picks Fallen"],
            data["fallers"],
            ["rank", "full_name", "position_name", "school_name", "picks_fallen"],
            count_label="prospects",
            empty_text="No fallers yet — check back once the draft is underway.",
            default_status="Undrafted",
        )

        picks_panel = filterable_table(
            "actual-picks",
            "Actual Picks",
            ["Pick", "Team", "Player", "Position", "School", "Status", "Tag", "Signability"],
            data["picks"],
            ["pick_number", "team_name", "player_name", "player_position", "school_name", "signed_status", "signability_tag", "signability"],
            count_label="picks",
            empty_text="No picks recorded yet for this draft year.",
            default_status="Drafted",
        )

        predictions_panel = filterable_table(
            "predictions",
            "Predictions",
            ["Pick", "Team", "Player", "Win Prob.", "Model"],
            data["predictions"],
            ["pick_number", "team_name", "player_name", "predicted_probability", "model_version"],
            count_label="predictions",
        )

        board_panel = filterable_table(
            "board",
            "Full Board (Top 250)",
            ["Rank", "Player", "Position", "School", "Drafted By", "Pick"],
            data["top_250"],
            ["rank", "full_name", "position_name", "school_name", "draft_team_name", "pick_number"],
            count_label="prospects",
        )

        def year_link(y):
            active = "active" if summary["year"] == y else ""
            return f'<a class="{active}" href="/?year={y}">{y}</a>'

        theme_attr = f' data-theme="{theme}"' if theme else ""
        theme_color = THEME_BG.get(theme, THEME_BG["dark"])
        body = f"""<!DOCTYPE html>
        <html lang="en"{theme_attr}>
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
          <title>MLB Draft Tracker</title>
          <link rel="icon" href="/favicon.ico" type="image/x-icon">
          <meta name="theme-color" content="{theme_color}">
          <style>{STYLE}</style>
          <script>
            (function () {{
              try {{
                // Server already rendered data-theme/theme-color correctly
                // from the mlb_theme cookie - nothing to do here except on
                // a first-ever visit (no cookie yet), where we still need a
                // client-side prefers-color-scheme fallback. iOS Safari's
                // notch/status-bar overlay is colored from theme-color's
                // value in the *initial* HTML response, not from later DOM
                // mutations, so getting this right server-side (rather than
                // patching the tag after the fact with JS) is what actually
                // keeps that overlay in sync with the chosen theme.
                if (!document.documentElement.hasAttribute('data-theme')) {{
                  var theme = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
                  document.documentElement.setAttribute('data-theme', theme);
                  document.cookie = 'mlb_theme=' + theme + '; path=/; max-age=31536000; SameSite=Lax';
                  var meta = document.querySelector('meta[name="theme-color"]');
                  var bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
                  if (meta && bg) meta.setAttribute('content', bg);
                }}
              }} catch (e) {{}}
            }})();
          </script>
        </head>
        <body>
          <header class="topbar">
            <div class="brand">
              <h1>MLB Draft Tracker <span class="build-version" title="Deployed commit">#{esc(GIT_COMMIT)}</span></h1>
              <span class="year-pill">Draft year {summary['year']} &middot; {summary['picks_made']} picks made</span>
            </div>
            <div class="topbar-controls">
              <label class="refresh-toggle">
                <input type="checkbox" id="auto-refresh">
                Auto-refresh (30s)
              </label>
              <label class="refresh-toggle" title="Also show a native browser notification when a new pick happens and this tab isn't focused">
                <input type="checkbox" id="pick-alerts-toggle">
                Pick alerts
              </label>
              <button type="button" id="theme-toggle" class="btn-ghost theme-toggle-btn" onclick="toggleTheme()" aria-label="Toggle light/dark theme">🌙</button>
              <nav class="year-nav">{year_link(2025)}{year_link(2026)}</nav>
            </div>
          </header>

          <nav class="subnav">
            <a href="#on-the-clock">On the Clock</a>
            <a href="#draft-order">Draft Order</a>
            <a href="#best-available">Best Available</a>
            <a href="#fallers">Fallers</a>
            <a href="#actual-picks">Actual Picks</a>
            <a href="#board">Full Board</a>
            <a href="#predictions">Predictions</a>
            <a href="#model-comparison">Model Comparison</a>
            <a href="#pool-tracker">Bonus Pool</a>
          </nav>

          <main>
            {schedule_banner}
            {cards}
            {on_the_clock}
            {draft_order_panel}
            {filter_bar}
            {best_available_panel}
            {fallers_panel}
            {picks_panel}
            {board_panel}
            {predictions_panel}
            {model_comparison_panel}
            {pool_tracker_panel}
          </main>

          <footer>MLB Draft Tracker &middot; local dashboard</footer>
          <div class="toast-container" id="toast-container"></div>

          <div class="prospect-card-overlay" id="prospect-card-overlay" onclick="hideProspectCard(event)">
            <div class="prospect-card" onclick="event.stopPropagation()">
              <button type="button" class="prospect-card-close" aria-label="Close" onclick="hideProspectCard()">&times;</button>
              <h3 id="prospect-card-name"></h3>
              <div class="prospect-card-meta">
                <img id="prospect-card-headshot" class="prospect-card-headshot" alt="" style="display:none">
                <p id="prospect-card-meta-line" class="prospect-card-meta-line"></p>
              </div>
              <div class="prospect-card-section">
                <h4>Blurb</h4>
                <p id="prospect-card-blurb"></p>
              </div>
              <div class="prospect-card-section">
                <h4>Scouting Report</h4>
                <p id="prospect-card-scouting"></p>
              </div>
              <div class="prospect-card-section" id="prospect-card-bonus-section" style="display:none">
                <h4>Signing Bonus</h4>
                <p id="prospect-card-bonus"></p>
              </div>
              <div class="prospect-card-section" id="prospect-card-signability-section" style="display:none">
                <h4>Signability</h4>
                <p id="prospect-card-signability-summary"></p>
                <ul id="prospect-card-signability-factors" class="prospect-card-factors"></ul>
              </div>
            </div>
          </div>

          <script>{SCRIPT}</script>
        </body>
        </html>
        """

        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body.encode("utf-8")]

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    init_db(args.db)
    with make_server(args.host, args.port, app_factory(args.db)) as httpd:
        print(f"Dashboard serving on http://{args.host}:{args.port}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
