from __future__ import annotations

import argparse
from pathlib import Path

from mlb_tracker.db import DEFAULT_DB_PATH, get_connection, init_db, insert_or_replace_predictions, upsert_draft_slot, upsert_prospect
from mlb_tracker.live_monitor import reconcile_live_picks
from mlb_tracker.mock_ingest import ingest_mock_assignments, seed_curated_mock_assignments
from mlb_tracker.no_r_ingest import build_no_r_seed
from mlb_tracker.predictions import generate_predictions
from mlb_tracker.sources import (
    fetch_baseballr_prospects_csv,
    normalize_prospect_row,
    seed_draft_slots_from_csv,
    seed_prospects_from_csv,
    verify_baseballr_setup,
)
from mlb_tracker.telegram import TelegramNotifier


ROOT = Path(__file__).resolve().parents[1]
SEED_ORDER = ROOT / "examples" / "draft_order_seed_2026.csv"
SEED_PROSPECTS_CSV = ROOT / "examples" / "prospects_top250_seed_2026.csv"


def cmd_init_db(args):
    init_db(args.db)
    print(f"Initialized DB at {args.db}")


def cmd_sync_prospects(args):
    init_db(args.db)
    conn = get_connection(args.db)
    rows = fetch_baseballr_prospects_csv(args.year)
    for raw in rows:
        upsert_prospect(conn, normalize_prospect_row(raw, args.year))
    conn.commit()
    conn.close()
    print(f"Synced {len(rows)} prospects for {args.year}")


def cmd_seed_no_r_prospects(args):
    init_db(args.db)
    conn = get_connection(args.db)
    rows = build_no_r_seed()
    for raw in rows:
        upsert_prospect(conn, normalize_prospect_row(raw, args.year))
    conn.commit()
    conn.close()
    print(f"Seeded {len(rows)} no-R prospects for {args.year}")


def cmd_seed_prospects_csv(args):
    init_db(args.db)
    conn = get_connection(args.db)
    rows = seed_prospects_from_csv(Path(args.csv), args.year)
    for row in rows:
        upsert_prospect(conn, row)
    conn.commit()
    conn.close()
    print(f"Seeded {len(rows)} prospects from {args.csv}")


def cmd_seed_draft_order(args):
    init_db(args.db)
    conn = get_connection(args.db)
    rows = seed_draft_slots_from_csv(Path(args.csv), args.year)
    for row in rows:
        upsert_draft_slot(conn, row)
    conn.commit()
    conn.close()
    print(f"Seeded {len(rows)} draft slots from {args.csv}")


def cmd_generate_predictions(args):
    init_db(args.db)
    conn = get_connection(args.db)
    rows = generate_predictions(conn, draft_year=args.year, top_n_per_pick=args.top_n, max_pick=args.max_pick)
    insert_or_replace_predictions(conn, rows, args.year, "heuristic_v1")
    conn.commit()
    conn.close()
    print(f"Generated {len(rows)} heuristic predictions")


def cmd_seed_mock_consensus(args):
    init_db(args.db)
    conn = get_connection(args.db)
    rows = ingest_mock_assignments(conn, seed_curated_mock_assignments(), draft_year=args.year)
    insert_or_replace_predictions(conn, rows, args.year, "mock_consensus_v1")
    conn.commit()
    conn.close()
    print(f"Seeded {len(rows)} mock-consensus predictions")


def cmd_live_monitor(args):
    init_db(args.db)
    conn = get_connection(args.db)
    notifier = TelegramNotifier()
    new_picks = reconcile_live_picks(conn, draft_year=args.year, notifier=notifier)
    conn.commit()
    conn.close()
    print(f"Observed {len(new_picks)} new picks")


def cmd_verify_baseballr(_args):
    ok, message = verify_baseballr_setup()
    if ok:
        print(message)
        return
    raise RuntimeError(message)


def cmd_test_telegram(args):
    notifier = TelegramNotifier()
    if not notifier.enabled:
        raise RuntimeError(
            "Telegram is not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
            "(see .env.example) and re-run this command."
        )
    result = notifier.send(args.message)
    print(f"Sent test message to Telegram: {result}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MLB Draft 2026 tracker")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("sync-prospects")
    p.add_argument("--year", type=int, default=2026)
    p.set_defaults(func=cmd_sync_prospects)

    p = sub.add_parser("seed-no-r-prospects")
    p.add_argument("--year", type=int, default=2026)
    p.set_defaults(func=cmd_seed_no_r_prospects)

    p = sub.add_parser("seed-prospects-csv")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--csv", default=str(SEED_PROSPECTS_CSV))
    p.set_defaults(func=cmd_seed_prospects_csv)

    p = sub.add_parser("seed-draft-order")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--csv", default=str(SEED_ORDER))
    p.set_defaults(func=cmd_seed_draft_order)

    p = sub.add_parser("generate-predictions")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--top-n", type=int, default=5)
    p.add_argument("--max-pick", type=int, default=50)
    p.set_defaults(func=cmd_generate_predictions)

    p = sub.add_parser("seed-mock-consensus")
    p.add_argument("--year", type=int, default=2026)
    p.set_defaults(func=cmd_seed_mock_consensus)

    p = sub.add_parser("live-monitor")
    p.add_argument("--year", type=int, default=2026)
    p.set_defaults(func=cmd_live_monitor)

    p = sub.add_parser("verify-baseballr")
    p.set_defaults(func=cmd_verify_baseballr)

    p = sub.add_parser("test-telegram")
    p.add_argument("--message", default="MLB Draft Tracker: this is a test notification.")
    p.set_defaults(func=cmd_test_telegram)
    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except RuntimeError as exc:
        parser.exit(status=1, message=f"ERROR: {exc}\n")
