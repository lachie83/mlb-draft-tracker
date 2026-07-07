from __future__ import annotations

import argparse
import html
import re
import sqlite3
from collections import defaultdict
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from mlb_tracker.db import DEFAULT_DB_PATH, get_connection, init_db


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


ROUND_LABEL_NAMES = {
    "PPI": "Prospect Promotion Incentive",
    "CB-A": "Competitive Balance Round A",
    "CB-B": "Competitive Balance Round B",
    "SUP-2": "Supplemental Round 2",
}


def round_display_name(round_label):
    if round_label in ROUND_LABEL_NAMES:
        return ROUND_LABEL_NAMES[round_label]
    if round_label and str(round_label).isdigit():
        return f"Round {round_label}"
    return f"Round {round_label}" if round_label else "Round"


def get_selected_year(environ, default_year: int = 2026) -> int:
    try:
        params = parse_qs(environ.get("QUERY_STRING", ""))
        year_str = params.get("year", [str(default_year)])[0]
        return int(year_str)
    except Exception:
        return default_year


def fetch_dashboard_data(conn: sqlite3.Connection, year: int):
    top_250 = [
        dict(r)
        for r in q(
            conn,
            """
            SELECT rank, full_name, position_name, school_name, school_class,
                   is_drafted, draft_team_name, pick_number
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
                   pros.current_age, pros.bats, pros.throws,
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
            SELECT pick_number, team_name, player_name, player_position, school_name
            FROM actual_picks
            WHERE draft_year = ?
            ORDER BY pick_number
            LIMIT 100
            """,
            (year,),
        )
    ]

    predictions = [
        dict(r)
        for r in q(
            conn,
            """
            SELECT pr.pick_number, pr.team_name, pr.player_name,
                   ROUND(pr.predicted_probability, 4) AS predicted_probability,
                   pr.model_version, p.position_name, p.school_class
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
        SELECT s.pick_number, s.team_name, s.round_label
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
            "candidates": candidates,
        }

    draft_order_rows = [
        dict(r)
        for r in q(
            conn,
            """
            SELECT s.pick_number, s.round_label, s.team_name, ap.player_name
            FROM draft_slots s
            LEFT JOIN actual_picks ap
                ON ap.draft_year = s.draft_year AND ap.pick_number = s.pick_number
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


def filterable_table(table_id, title, headers, rows, cell_keys, *, count_label="rows", empty_text="No matching rows.", default_status=None):
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
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
        cells = "".join(f"<td>{esc(row.get(k, ''))}</td>" for k in cell_keys)
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
              <td>{esc(c.get('player_name'))}</td>
              <td>{esc(c.get('position_name'))}</td>
              <td>{esc(c.get('school_name'))}</td>
              <td>{esc(c.get('predicted_probability'))}</td>
              <td>{'<span class="badge badge-accent">Agree</span>' if c.get('model_count', 1) > 1 else ''}</td>
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
      <p class="on-the-clock-team">{esc(otc['team_name'])} <span class="muted">&middot; {esc(otc.get('round_label') or '')}</span></p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Player</th><th>Position</th><th>School</th><th>Win Prob.</th><th>Models</th></tr></thead>
          <tbody>{candidate_rows}</tbody>
        </table>
      </div>
    </section>
    """


def render_draft_order(groups, on_the_clock_pick_number):
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
            f"""<tr class="{'otc-row' if p['pick_number'] == on_the_clock_pick_number else ''}">
                  <td>{esc(p['pick_number'])}</td>
                  <td>{esc(p['team_name'])}</td>
                  <td>{esc(p.get('player_name')) or ('<span class="badge badge-accent">On the Clock</span>' if p['pick_number'] == on_the_clock_pick_number else '<span class="muted">&mdash;</span>')}</td>
                </tr>"""
            for p in group["picks"]
        )
        round_panels.append(
            f"""
            <div class="round-panel" id="{slug}"{'' if is_active else ' style="display:none"'}>
              <h3 class="round-heading">{esc(group['round_name'])}</h3>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Pick</th><th>Team</th><th>Player</th></tr></thead>
                  <tbody>{pick_rows}</tbody>
                </table>
              </div>
            </div>
            """
        )

    total_picks = sum(len(group["picks"]) for group in groups)
    return f"""
    <section class="panel" id="draft-order">
      <div class="panel-header">
        <h2>Draft Order</h2>
        <span class="badge">{total_picks} picks</span>
      </div>
      <div class="round-tabs">{''.join(tab_buttons)}</div>
      {''.join(round_panels)}
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
    <div class="filter-bar">
      <div class="filter-group">
        <label for="f-search">Search</label>
        <input id="f-search" type="text" placeholder="Search player, school, team&hellip;" oninput="applyFilters()">
      </div>
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
      <button type="button" class="btn-ghost" onclick="resetFilters()">Reset</button>
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
                cells.append(f"<td>{esc(pick['player_name'])} <span class=\"muted\">({esc(pick['predicted_probability'])})</span></td>")
            else:
                cells.append('<td class="muted">&mdash;</td>')
        agreement_cell = '<td><span class="badge badge-accent">Agree</span></td>' if agree else "<td></td>"
        body_rows.append(
            f"<tr><td>{esc(row['pick_number'])}</td><td>{esc(row['team_name'])}</td>{''.join(cells)}{agreement_cell}</tr>"
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
:root {
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
}

* { box-sizing: border-box; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  margin: 0;
  background: radial-gradient(circle at top, #101a30 0%, var(--bg) 55%);
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
  background: rgba(11, 17, 32, 0.92);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--border);
}

.brand { display: flex; align-items: baseline; gap: 10px; }
.brand h1 { font-size: 19px; margin: 0; letter-spacing: 0.2px; }
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
  background: rgba(11, 17, 32, 0.85);
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

.on-the-clock-team { font-size: 20px; font-weight: 700; margin: 0 0 14px; }

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
tbody tr { border-bottom: 1px solid rgba(37, 51, 82, 0.6); }
tbody tr:hover { background: rgba(59, 130, 246, 0.08); }
tbody tr:nth-child(even) { background: rgba(255, 255, 255, 0.015); }

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

.muted { color: var(--text-muted); }

footer { text-align: center; padding: 20px; color: var(--text-muted); font-size: 12px; }

@media (max-width: 720px) {
  .topbar, .subnav, main { padding-left: 16px; padding-right: 16px; }
  .filter-bar { position: static; }
  .subnav { top: 0; }
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

function showRound(btn) {
  const target = btn.getAttribute('data-target');
  document.querySelectorAll('.round-tab').forEach(function (t) {
    t.classList.toggle('active', t === btn);
  });
  document.querySelectorAll('.round-panel').forEach(function (p) {
    p.style.display = (p.id === target) ? '' : 'none';
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

document.addEventListener('DOMContentLoaded', function () {
  initAutoRefresh();
  updateEmptyStates();
});
"""


def app_factory(db_path: str):
    def app(environ, start_response):
        init_db(db_path)
        year = get_selected_year(environ, default_year=2026)
        conn = get_connection(db_path)
        data = fetch_dashboard_data(conn, year)
        conn.close()

        summary = data["summary"]

        cards = f"""
        <div class="cards">
          <div class="card"><h3>Prospects Loaded</h3><div class="value">{summary['prospects_loaded']}</div></div>
          <div class="card"><h3>Actual Picks Loaded</h3><div class="value">{summary['picks_loaded']}</div></div>
          <div class="card"><h3>Predictions Loaded</h3><div class="value">{summary['predictions_loaded']}</div></div>
          <div class="card"><h3>Top Available</h3><div class="value">{esc(summary['top_ranked_available'])}</div></div>
        </div>
        """

        filter_bar = render_filter_bar(data["positions"], data["teams"], data["models"])
        on_the_clock = render_on_the_clock(data["on_the_clock"])
        otc_pick_number = data["on_the_clock"]["pick_number"] if data["on_the_clock"] else None
        draft_order_panel = render_draft_order(data["draft_order"], otc_pick_number)
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
            ["Pick", "Team", "Player", "Position", "School"],
            data["picks"],
            ["pick_number", "team_name", "player_name", "player_position", "school_name"],
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

        body = f"""<!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <title>MLB Draft Tracker</title>
          <style>{STYLE}</style>
        </head>
        <body>
          <header class="topbar">
            <div class="brand">
              <h1>MLB Draft Tracker</h1>
              <span class="year-pill">Draft year {summary['year']} &middot; {summary['picks_made']} picks made</span>
            </div>
            <div class="topbar-controls">
              <label class="refresh-toggle">
                <input type="checkbox" id="auto-refresh">
                Auto-refresh (30s)
              </label>
              <nav class="year-nav">{year_link(2025)}{year_link(2026)}</nav>
            </div>
          </header>

          <nav class="subnav">
            <a href="#on-the-clock">On the Clock</a>
            <a href="#draft-order">Draft Order</a>
            <a href="#best-available">Best Available</a>
            <a href="#fallers">Fallers</a>
            <a href="#actual-picks">Actual Picks</a>
            <a href="#predictions">Predictions</a>
            <a href="#model-comparison">Model Comparison</a>
            <a href="#board">Full Board</a>
          </nav>

          <main>
            {cards}
            {on_the_clock}
            {draft_order_panel}
            {filter_bar}
            {best_available_panel}
            {fallers_panel}
            {picks_panel}
            {predictions_panel}
            {model_comparison_panel}
            {board_panel}
          </main>

          <footer>MLB Draft Tracker &middot; local dashboard</footer>
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
