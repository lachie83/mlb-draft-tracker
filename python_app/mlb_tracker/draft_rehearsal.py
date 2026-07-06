"""Draft-day rehearsal: replay a real, completed past draft (e.g. 2025)
through the exact same reconciliation path used on the real day
(`mlb_stats_api.reconcile_picks_from_api`), revealing its picks gradually
instead of all at once.

This exercises the whole pipeline end to end before it matters: draft order
seeding, live pick detection, prospect/actual_pick upserts, Telegram
alerts, and dashboard rendering (point dashboard.py at the rehearsal DB and
watch picks appear in real time).

Safety: always rehearse into a dedicated database and a draft_year that
can't collide with a real draft (see main.py's `rehearse-draft-day`
command, which defaults to draft_year=9999 for exactly this reason).
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .db import upsert_draft_slot
from .mlb_stats_api import fetch_draft, iter_picks, pick_to_draft_slot, reconcile_picks_from_api
from .telegram import TelegramNotifier


class ReplayClient:
    """Drop-in replacement for HttpClient: instead of hitting the network,
    reveals picks from a pre-fetched draft payload a batch at a time. Used
    as the `client` passed to mlb_stats_api.fetch_draft()."""

    def __init__(self, source_payload: dict[str, Any], batch_size: int = 1, max_reveal: int | None = None):
        self.batch_size = max(1, batch_size)
        self.all_picks = sorted(iter_picks(source_payload), key=lambda p: p["pickNumber"])
        self.max_reveal = min(max_reveal, len(self.all_picks)) if max_reveal else len(self.all_picks)
        self.revealed = 0

    def is_finished(self) -> bool:
        return self.revealed >= self.max_reveal

    def get_json(self, _url: str) -> dict[str, Any]:
        self.revealed = min(self.revealed + self.batch_size, self.max_reveal)
        return {"drafts": {"rounds": [{"round": "replay", "picks": self.all_picks[: self.revealed]}]}}


def rehearse_draft_day(
    conn,
    source_year: int,
    target_year: int,
    picks: int | None = None,
    batch_size: int = 1,
    delay_seconds: float = 5.0,
    notifier: TelegramNotifier | None = None,
    on_tick: Callable[[list[dict[str, Any]], int, int], None] | None = None,
) -> int:
    """Seed target_year's draft_slots from source_year's real order, then
    reveal source_year's real picks into target_year a batch at a time,
    running the normal reconcile/Telegram-alert path on each reveal.
    Returns the total number of picks simulated."""
    source_payload = fetch_draft(source_year)

    # Seed the full schedule upfront, same as real draft day: the pick order
    # is public before the draft starts, only the picks themselves are
    # revealed live.
    for pick in iter_picks(source_payload):
        upsert_draft_slot(conn, pick_to_draft_slot(pick, target_year))
    conn.commit()

    replay = ReplayClient(source_payload, batch_size=batch_size, max_reveal=picks)
    total_simulated = 0
    while not replay.is_finished():
        new_picks = reconcile_picks_from_api(conn, draft_year=target_year, notifier=notifier, client=replay)
        conn.commit()
        total_simulated += len(new_picks)
        if on_tick:
            on_tick(new_picks, replay.revealed, replay.max_reveal)
        if not replay.is_finished():
            time.sleep(delay_seconds)
    return total_simulated
