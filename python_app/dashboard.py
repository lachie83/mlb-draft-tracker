from __future__ import annotations

import argparse
import html
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from wsgiref.simple_server import make_server

from mlb_tracker.db import DEFAULT_DB_PATH, get_connection, init_db


def q(conn: sqlite3.Connection, sql: str, params=()):
    return conn.execute(sql, params).fetchall()


def esc(value):
    if value is None:
        return ""
    return html.escape(str(value))


def table(headers, rows):
    head = ''.join(f'<th>{esc(h)}</th>' for h in headers)
    body = []
    for row in rows:
        cols = ''.join(f'<td>{esc(row.get(h, ""))}</td>' for h in headers)
        body.append(f'<tr>{cols}</tr>')
    return f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def fetch_dashboard_data(conn: sqlite3.Connection):
    top_250 = [dict(r) for r in q(conn, 'SELECT rank, full_name, position_name, school_name, draft_team_name, pick_number FROM prospects WHERE draft_year = 2026 AND rank IS NOT NULL ORDER BY rank LIMIT 250')]
    best_available = [dict(r) for r in q(conn, 'SELECT rank, full_name, position_name, school_name FROM prospects WHERE draft_year = 2026 AND rank IS NOT NULL AND COALESCE(is_drafted,0)=0 ORDER BY rank LIMIT 25')]
    picks = [dict(r) for r in q(conn, 'SELECT pick_number, team_name, player_name, player_position, school_name FROM actual_picks WHERE draft_year = 2026 ORDER BY pick_number LIMIT 100')]
    predictions = [dict(r) for r in q(conn, 'SELECT pick_number, team_name, player_name, ROUND(predicted_probability,4) AS predicted_probability, model_version FROM predictions WHERE draft_year = 2026 ORDER BY pick_number, predicted_probability DESC LIMIT 100')]
    summary = {
        'prospects_loaded': q(conn, 'SELECT COUNT(*) AS c FROM prospects WHERE draft_year = 2026')[0]['c'],
        'picks_loaded': q(conn, 'SELECT COUNT(*) AS c FROM actual_picks WHERE draft_year = 2026')[0]['c'],
        'predictions_loaded': q(conn, 'SELECT COUNT(*) AS c FROM predictions WHERE draft_year = 2026')[0]['c'],
        'top_ranked_available': (q(conn, 'SELECT full_name FROM prospects WHERE draft_year = 2026 AND rank IS NOT NULL AND COALESCE(is_drafted,0)=0 ORDER BY rank LIMIT 1')[0]['full_name'] if q(conn, 'SELECT full_name FROM prospects WHERE draft_year = 2026 AND rank IS NOT NULL AND COALESCE(is_drafted,0)=0 ORDER BY rank LIMIT 1') else 'N/A')
    }
    return summary, top_250, best_available, picks, predictions


def app_factory(db_path: str):
    def app(environ, start_response):
        init_db(db_path)
        conn = get_connection(db_path)
        summary, top_250, best_available, picks, predictions = fetch_dashboard_data(conn)
        conn.close()
        body = f"""
        <html>
        <head>
          <title>MLB Draft 2026 Tracker</title>
          <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background: #0f172a; color: #e2e8f0; }}
            h1,h2,h3 {{ color: #f8fafc; }}
            .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
            .card {{ background: #1e293b; padding: 16px; border-radius: 10px; min-width: 220px; }}
            table {{ border-collapse: collapse; width: 100%; margin-bottom: 28px; background: #111827; }}
            th, td {{ border: 1px solid #334155; padding: 8px; text-align: left; font-size: 14px; }}
            th {{ background: #1d4ed8; color: white; position: sticky; top: 0; }}
            a {{ color: #93c5fd; }}
          </style>
        </head>
        <body>
          <h1>MLB Draft 2026 Tracker Dashboard</h1>
          <div class="cards">
            <div class="card"><h3>Prospects Loaded</h3><div>{summary['prospects_loaded']}</div></div>
            <div class="card"><h3>Actual Picks Loaded</h3><div>{summary['picks_loaded']}</div></div>
            <div class="card"><h3>Predictions Loaded</h3><div>{summary['predictions_loaded']}</div></div>
            <div class="card"><h3>Top Available</h3><div>{esc(summary['top_ranked_available'])}</div></div>
          </div>
          <h2>Best Available</h2>
          {table(['rank','full_name','position_name','school_name'], best_available)}
          <h2>Actual Picks</h2>
          {table(['pick_number','team_name','player_name','player_position','school_name'], picks)}
          <h2>Predictions</h2>
          {table(['pick_number','team_name','player_name','predicted_probability','model_version'], predictions)}
          <h2>Top 250 Board</h2>
          {table(['rank','full_name','position_name','school_name','draft_team_name','pick_number'], top_250)}
        </body>
        </html>
        """
        start_response('200 OK', [('Content-Type', 'text/html; charset=utf-8')])
        return [body.encode('utf-8')]
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=str(DEFAULT_DB_PATH))
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    init_db(args.db)
    with make_server(args.host, args.port, app_factory(args.db)) as httpd:
        print(f'Dashboard serving on http://{args.host}:{args.port}')
        httpd.serve_forever()


if __name__ == '__main__':
    main()
