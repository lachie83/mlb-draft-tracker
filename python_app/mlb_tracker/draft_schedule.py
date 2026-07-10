"""The 2026 MLB Draft's real broadcast/pick-window schedule (Pennsylvania
Convention Center, Philadelphia), supplied by the user from MLB's official
broadcast schedule. All times are America/New_York (the draft's home tz),
matching how MLB itself publishes these windows.

Pick-count boundaries (1-10 / 11-40 / 41-135 / 136+) were cross-checked
against the actual completed 2025 draft's real pick numbers via the live
MLB Stats API on 2026-07-10: rounds 1-4 ended at pick 135 and rounds 5-20
covered picks 136-615 (480 picks) in that draft, exactly matching the
boundaries given for 2026's Day 1/Day 2 split. That check also revealed
Day 2 (11:30a-7:30p, 480 picks over 8 hours = ~1 pick/minute) is actually
the single densest window of the whole draft, denser than the "picks
41-135" Day 1 block (95 picks over ~3h15m = ~0.49 picks/minute) - counter
to the intuition that the televised early rounds move fastest. Rounds 5-20
have no broadcast pacing at all, just teams submitting picks back-to-back
online, which is what makes it faster per-minute despite being the "slow"
rounds. poll_interval_seconds below reflects the real measured density of
each window, not the assumption that TV coverage implies more polling.

This module is pure/no I/O by design (data + functions of an injectable
`now`) so it's fully unit-testable without touching a clock or a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

DRAFT_2026_LOCATION = "Pennsylvania Convention Center, Philadelphia"

# Baseline cadence when no milestone window is active (pre-draft, overnight
# between Day 1 and Day 2, or after the draft has wrapped) - matches
# poll_draft_day.sh's long-standing default.
DEFAULT_POLL_INTERVAL_SECONDS = 60


@dataclass(frozen=True)
class DraftMilestone:
    key: str
    label: str
    picks_label: str
    channels: str
    start: datetime
    end: datetime
    poll_interval_seconds: int


DRAFT_2026_MILESTONES: list[DraftMilestone] = [
    DraftMilestone(
        key="day1_preview",
        label="Day 1: Preview Show",
        picks_label="Picks 1-10",
        channels="NBC / Peacock",
        start=datetime(2026, 7, 11, 13, 0, tzinfo=ET),
        end=datetime(2026, 7, 11, 14, 30, tzinfo=ET),
        poll_interval_seconds=45,
    ),
    DraftMilestone(
        key="day1_early",
        label="Day 1: Early First Round",
        picks_label="Picks 11-40",
        channels="MLB Network / MLB.com / MLB.TV / MLB+",
        start=datetime(2026, 7, 11, 14, 30, tzinfo=ET),
        end=datetime(2026, 7, 11, 16, 30, tzinfo=ET),
        poll_interval_seconds=30,
    ),
    DraftMilestone(
        key="day1_late",
        label="Day 1: Rounds 2-4",
        picks_label="Picks 41-135",
        channels="MLB.com / MLB.TV / MLB+",
        start=datetime(2026, 7, 11, 16, 30, tzinfo=ET),
        end=datetime(2026, 7, 11, 19, 45, tzinfo=ET),
        poll_interval_seconds=15,
    ),
    DraftMilestone(
        key="day2",
        label="Day 2: Rounds 5-20",
        picks_label="Picks 136+",
        channels="MLB.com / MLB.TV / MLB+",
        start=datetime(2026, 7, 12, 11, 30, tzinfo=ET),
        end=datetime(2026, 7, 12, 19, 30, tzinfo=ET),
        # Real measured density (see module docstring): ~1 pick/minute,
        # the fastest window of the whole draft despite being "just" the
        # later rounds - polls the most aggressively of any window.
        poll_interval_seconds=10,
    ),
]


def _as_et(now: datetime | None) -> datetime:
    now = now or datetime.now(ET)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ET)


def current_milestone(now: datetime | None = None) -> DraftMilestone | None:
    """The milestone window active right now, or None outside all of them.
    Start is inclusive, end is exclusive, so back-to-back windows never
    overlap or leave a gap at the boundary."""
    now = _as_et(now)
    for milestone in DRAFT_2026_MILESTONES:
        if milestone.start <= now < milestone.end:
            return milestone
    return None


def next_milestone(now: datetime | None = None) -> DraftMilestone | None:
    """The soonest upcoming milestone, or None once the last one has ended."""
    now = _as_et(now)
    upcoming = [m for m in DRAFT_2026_MILESTONES if m.start > now]
    return min(upcoming, key=lambda m: m.start) if upcoming else None


def recommended_poll_interval_seconds(now: datetime | None = None) -> int:
    milestone = current_milestone(now)
    return milestone.poll_interval_seconds if milestone else DEFAULT_POLL_INTERVAL_SECONDS
