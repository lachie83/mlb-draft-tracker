from __future__ import annotations

import argparse
from pathlib import Path

from mlb_tracker.db import DEFAULT_DB_PATH, get_connection, init_db, insert_or_replace_predictions, upsert_draft_slot, upsert_prospect
from mlb_tracker.draft_rehearsal import cleanup_rehearsal_data, rehearse_draft_day
from mlb_tracker.live_monitor import reconcile_live_picks
from mlb_tracker.mlb_stats_api import (
    fetch_latest,
    get_on_the_clock,
    reconcile_picks_from_api,
    sync_draft_order,
    sync_prospects_from_api,
)
from mlb_tracker.mock_ingest import generate_mock_consensus_predictions, ingest_real_mock_draft_picks
from mlb_tracker.no_r_ingest import build_no_r_seed
from mlb_tracker.predictions import generate_predictions
from mlb_tracker.sources import (
    fetch_baseballr_prospects_csv,
    normalize_prospect_row,
    seed_draft_slots_from_csv,
    seed_prospects_from_csv,
    verify_baseballr_setup,
)
from mlb_tracker.telegram import TelegramNotifier, format_prospect_changes_message


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


def cmd_seed_mock_drafts(args):
    init_db(args.db)
    conn = get_connection(args.db)
    rows = ingest_real_mock_draft_picks(conn, draft_year=args.year)
    conn.commit()
    conn.close()
    print(f"Seeded {len(rows)} mock draft pick observations for {args.year}")


def cmd_seed_mock_consensus(args):
    init_db(args.db)
    conn = get_connection(args.db)
    rows = generate_mock_consensus_predictions(conn, draft_year=args.year, top_n_per_pick=args.top_n)
    insert_or_replace_predictions(conn, rows, args.year, "mock_consensus_v2")
    conn.commit()
    conn.close()
    print(f"Generated {len(rows)} mock-consensus predictions from mock_draft_picks (run seed-mock-drafts first if this is 0)")


def cmd_live_monitor(args):
    init_db(args.db)
    conn = get_connection(args.db)
    notifier = TelegramNotifier()
    new_picks = reconcile_live_picks(conn, draft_year=args.year, notifier=notifier)
    conn.commit()
    conn.close()
    print(f"Observed {len(new_picks)} new picks")


def cmd_sync_draft_order_api(args):
    init_db(args.db)
    conn = get_connection(args.db)
    rows = sync_draft_order(conn, draft_year=args.year)
    conn.commit()
    conn.close()
    print(f"Synced {len(rows)} draft slots from the MLB Stats API for {args.year}")


def cmd_sync_prospects_api(args):
    init_db(args.db)
    conn = get_connection(args.db)
    result = sync_prospects_from_api(conn, draft_year=args.year)
    conn.close()
    print(
        f"Synced {result['total_synced']} prospects ({result['ranked_count']} ranked) "
        f"from the MLB Stats API for {args.year}"
    )

    message = format_prospect_changes_message(args.year, result)
    if message is None:
        print("No change to the ranked board since the last sync.")
        return
    print(message)
    if args.notify:
        notifier = TelegramNotifier()
        print(f"Telegram: {notifier.send(message)}")
    else:
        print("(--no-notify passed, not sending to Telegram)")


def cmd_live_monitor_api(args):
    init_db(args.db)
    conn = get_connection(args.db)
    notifier = TelegramNotifier()
    new_picks = reconcile_picks_from_api(conn, draft_year=args.year, notifier=notifier)
    conn.commit()
    conn.close()
    print(f"Observed {len(new_picks)} new picks via the MLB Stats API")


def cmd_on_the_clock_api(args):
    payload = fetch_latest(args.year)
    on_the_clock = get_on_the_clock(payload)
    if not on_the_clock:
        print("No upcoming picks reported (draft may not be live right now).")
        return
    for pick in on_the_clock:
        team = pick.get("team") or {}
        print(f"Pick #{pick.get('pickNumber')} ({pick.get('pickRound')}): {team.get('name', 'Unknown team')}")


def cmd_rehearse_draft_day(args):
    if args.year in (2025, 2026):
        print(
            f"WARNING: --year {args.year} looks like a real draft year. Rehearsal data "
            "will be written into that year's rows in this database, mixed in with real "
            "data. Strongly recommended: leave --year at its default (9999) and/or pass "
            "--db pointing at a dedicated rehearsal database, e.g. --db ../data/rehearsal.db"
        )
    init_db(args.db)
    conn = get_connection(args.db)
    notifier = TelegramNotifier()
    if notifier.enabled:
        print(f"Telegram is configured: this rehearsal will send up to {args.picks} real alert(s) to your chat.")
    else:
        print("Telegram is not configured: picks will be simulated without sending alerts.")
    print(f"Rehearsing up to {args.picks} picks from the {args.source_year} draft into draft_year={args.year} at {args.db} ...")

    def on_tick(new_picks, revealed, total):
        for pick in new_picks:
            print(f"  [{revealed}/{total}] Pick #{pick['pick_number']}: {pick['team_name']} selects {pick['player_name']}")

    total_simulated = rehearse_draft_day(
        conn,
        source_year=args.source_year,
        target_year=args.year,
        picks=args.picks,
        batch_size=args.batch_size,
        delay_seconds=args.delay,
        notifier=notifier,
        on_tick=on_tick,
    )
    conn.close()
    print(f"Rehearsal complete: simulated {total_simulated} picks.")


def cmd_rehearse_draft_day_cleanup(args):
    init_db(args.db)
    conn = get_connection(args.db)
    deleted = cleanup_rehearsal_data(conn, year=args.year)
    conn.close()
    for table, count in deleted.items():
        print(f"  {table}: {count} row(s) deleted")
    print(f"Cleanup complete: {sum(deleted.values())} row(s) removed for draft_year={args.year} at {args.db}.")


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

    p = sub.add_parser("seed-mock-drafts")
    p.add_argument("--year", type=int, default=2026)
    p.set_defaults(func=cmd_seed_mock_drafts)

    p = sub.add_parser("seed-mock-consensus")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--top-n", type=int, default=5)
    p.set_defaults(func=cmd_seed_mock_consensus)

    p = sub.add_parser("live-monitor")
    p.add_argument("--year", type=int, default=2026)
    p.set_defaults(func=cmd_live_monitor)

    p = sub.add_parser("sync-draft-order-api")
    p.add_argument("--year", type=int, default=2026)
    p.set_defaults(func=cmd_sync_draft_order_api)

    p = sub.add_parser("sync-prospects-api")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument(
        "--notify", action=argparse.BooleanOptionalAction, default=True,
        help="send a Telegram alert if the ranked board changed since the last sync (default: yes)",
    )
    p.set_defaults(func=cmd_sync_prospects_api)

    p = sub.add_parser("live-monitor-api")
    p.add_argument("--year", type=int, default=2026)
    p.set_defaults(func=cmd_live_monitor_api)

    p = sub.add_parser("on-the-clock-api")
    p.add_argument("--year", type=int, default=2026)
    p.set_defaults(func=cmd_on_the_clock_api)

    p = sub.add_parser("rehearse-draft-day")
    p.add_argument("--year", type=int, default=9999, help="draft_year tag for simulated picks (default 9999 - a sentinel that can't collide with a real draft)")
    p.add_argument("--source-year", type=int, default=2025, help="completed draft year to replay real picks from")
    p.add_argument("--picks", type=int, default=10, help="number of picks to simulate (default 10; the full source draft has 600+)")
    p.add_argument("--batch-size", type=int, default=1, help="picks revealed per tick (default 1)")
    p.add_argument("--delay", type=float, default=5.0, help="seconds between simulated picks (default 5.0)")
    p.set_defaults(func=cmd_rehearse_draft_day)

    p = sub.add_parser("rehearse-draft-day-cleanup")
    p.add_argument("--year", type=int, default=9999, help="draft_year tag to delete rehearsal rows for (default 9999, matching rehearse-draft-day's sentinel)")
    p.set_defaults(func=cmd_rehearse_draft_day_cleanup)

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
