"""Real, dated, attributed 2026 MLB mock draft picks.

Each entry below is a faithful transcription of a specific published mock
draft — not a synthetic/curated approximation. They were retrieved by
browsing MLB Pipeline's coverage directly:

- "MLB Pipeline 2026 mock draft July 2" by Jonathan Mayo, published
  2026-07-02: https://www.mlb.com/milb/news/mlb-pipeline-2026-mock-draft-july-2
- "Our experts tag-team the latest mock draft from the Combine" by
  Jonathan Mayo, Jim Callis, and Brendan Samson, published 2026-06-25:
  https://www.mlb.com/milb/news/mlb-pipeline-2026-mock-draft-june-25

These are point-in-time snapshots, like examples/prospects_top250_seed_2026.csv
- they will not reflect mock drafts published after the date this module was
written, and should be refreshed (or extended with additional sources) as new
mocks are published.

`board_rank` is the prospect's rank as cited in that specific mock article
("(No. X)"), which can differ between mocks/dates as the board is re-ranked.
`weight` is a per-source confidence multiplier (more recent mocks are closer
to the draft and weighted higher); for MLB Pipeline's July 2 mock, pick 1 was
given as an explicit three-way percentage split by the author, which is
encoded here as three separate weighted rows rather than one.
"""

from __future__ import annotations

from typing import Any

# Maps the short team nicknames used in mock draft prose to the full team
# names used elsewhere in this app's data (e.g. examples/draft_order_seed_2026.csv).
TEAM_NICKNAME_TO_FULL_NAME: dict[str, str] = {
    "White Sox": "Chicago White Sox",
    "Rays": "Tampa Bay Rays",
    "Twins": "Minnesota Twins",
    "Giants": "San Francisco Giants",
    "Pirates": "Pittsburgh Pirates",
    "Royals": "Kansas City Royals",
    "Orioles": "Baltimore Orioles",
    "Athletics": "Athletics",
    "Braves": "Atlanta Braves",
    "Rockies": "Colorado Rockies",
    "Nationals": "Washington Nationals",
    "Angels": "Los Angeles Angels",
    "Cardinals": "St. Louis Cardinals",
    "Marlins": "Miami Marlins",
    "Diamondbacks": "Arizona Diamondbacks",
    "D-backs": "Arizona Diamondbacks",
    "Rangers": "Texas Rangers",
    "Astros": "Houston Astros",
    "Reds": "Cincinnati Reds",
    "Guardians": "Cleveland Guardians",
    "Red Sox": "Boston Red Sox",
    "Padres": "San Diego Padres",
    "Tigers": "Detroit Tigers",
    "Cubs": "Chicago Cubs",
    "Mariners": "Seattle Mariners",
    "Brewers": "Milwaukee Brewers",
    "Mets": "New York Mets",
    "Yankees": "New York Yankees",
    "Phillies": "Philadelphia Phillies",
    "Blue Jays": "Toronto Blue Jays",
    "Dodgers": "Los Angeles Dodgers",
}


def full_team_name(nickname: str) -> str:
    return TEAM_NICKNAME_TO_FULL_NAME.get(nickname, nickname)


_JULY_2_SOURCE = {
    "source_name": "MLB Pipeline Mock Draft",
    "source_authors": "Jonathan Mayo",
    "source_date": "2026-07-02",
    "source_url": "https://www.mlb.com/milb/news/mlb-pipeline-2026-mock-draft-july-2",
    "weight": 1.5,
}

_JUNE_25_SOURCE = {
    "source_name": "MLB Pipeline Mock Draft",
    "source_authors": "Jonathan Mayo, Jim Callis, Brendan Samson",
    "source_date": "2026-06-25",
    "source_url": "https://www.mlb.com/milb/news/mlb-pipeline-2026-mock-draft-june-25",
    "weight": 1.0,
}

# (pick_number, team_nickname, player_name, board_rank)
_JULY_2_PICKS: list[tuple[int, str, str, int]] = [
    (2, "Rays", "Grady Emerson", 1),
    (3, "Twins", "Vahn Lackey", 3),
    (4, "Giants", "Jacob Lombard", 5),
    (5, "Pirates", "Eric Booth Jr.", 6),
    (6, "Royals", "Jackson Flora", 4),
    (7, "Orioles", "Drew Burress", 7),
    (8, "Athletics", "Ryder Helfrick", 11),
    (9, "Braves", "Gio Rojas", 8),
    (10, "Rockies", "AJ Gracia", 19),
    (11, "Nationals", "Ace Reese", 18),
    (12, "Angels", "Derek Curiel", 12),
    (13, "Cardinals", "Tyler Bell", 10),
    (14, "Marlins", "Chris Hacopian", 14),
    (15, "Diamondbacks", "Justin Lebron", 9),
    (16, "Rangers", "Jared Grindlinger", 16),
    (17, "Astros", "Liam Peterson", 20),
    (18, "Reds", "Trevor Condon", 13),
    (19, "Guardians", "Sawyer Strosnider", 22),
    (20, "Red Sox", "Hunter Dietz", 17),
    (21, "Padres", "Carson Bolemon", 24),
    (22, "Tigers", "Aiden Ruiz", 32),
    (23, "Cubs", "Cameron Flukey", 15),
    (24, "Mariners", "Tegan Kuhns", 25),
    (25, "Brewers", "Zion Rose", 30),
    # Supplemental first round
    (26, "Braves", "Mason Edwards", 36),
    (27, "Mets", "Bo Lowrance", 21),
    (28, "Astros", "Daniel Jackson", 28),
    (29, "Giants", "Brody Bumila", 23),
    (30, "Royals", "Cole Carlon", 26),
    (31, "Diamondbacks", "Taj Marchand", 37),
    (32, "Cardinals", "Logan Reddemann", 31),
    (33, "Rays", "Cole Prosek", 27),
    (34, "Pirates", "Logan Schmidt", 45),
    (35, "Yankees", "Cameron Borthwick", 43),
    (36, "Phillies", "Jack Radel", 44),
    (37, "Rockies", "Cade Townsend", 35),
    (38, "Rockies", "Chase Brunson", 50),
    (39, "Blue Jays", "Ty Head", 60),
    (40, "Dodgers", "Aiden Robbins", 29),
]

# Pick 1 was given as an explicit three-way percentage split rather than a
# single projected player.
_JULY_2_PICK_1_SPLIT: list[tuple[str, float, int]] = [
    ("Roch Cholowsky", 0.50, 2),
    ("Grady Emerson", 0.45, 1),
    ("Vahn Lackey", 0.05, 3),
]

_JUNE_25_PICKS: list[tuple[int, str, str, int]] = [
    (1, "White Sox", "Roch Cholowsky", 1),
    (2, "Rays", "Vahn Lackey", 3),
    (3, "Twins", "Grady Emerson", 2),
    (4, "Giants", "Jacob Lombard", 4),
    (5, "Pirates", "Jackson Flora", 5),
    (6, "Royals", "Eric Booth Jr.", 6),
    (7, "Orioles", "Drew Burress", 7),
    (8, "Athletics", "Ryder Helfrick", 13),
    (9, "Braves", "Gio Rojas", 8),
    (10, "Rockies", "Derek Curiel", 12),
    (11, "Nationals", "Tyler Bell", 20),
    (12, "Angels", "Chris Hacopian", 10),
    (13, "Cardinals", "Jared Grindlinger", 18),
    (14, "Marlins", "Ace Reese", 21),
    (15, "Diamondbacks", "AJ Gracia", 17),
    (16, "Rangers", "Justin Lebron", 9),
    (17, "Astros", "Liam Peterson", 14),
    (18, "Reds", "Cameron Flukey", 11),
    (19, "Guardians", "Trevor Condon", 22),
    (20, "Red Sox", "Bo Lowrance", 38),
    (21, "Padres", "Cole Prosek", 33),
    (22, "Tigers", "Landon Thome", 37),
    (23, "Cubs", "Daniel Jackson", 39),
    (24, "Mariners", "Tegan Kuhns", 24),
    (25, "Brewers", "Sawyer Strosnider", 16),
    # Only a subset of supplemental picks were covered in this mock.
    (27, "Mets", "Aiden Robbins", 30),
    (35, "Yankees", "Zion Rose", 31),
    (36, "Phillies", "Cade Townsend", 27),
    (39, "Blue Jays", "Logan Reddemann", 28),
    (40, "Dodgers", "Will Brick", 51),
]


def _build_picks() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for pick_number, nickname, player_name, board_rank in _JULY_2_PICKS:
        rows.append(
            {
                **_JULY_2_SOURCE,
                "pick_number": pick_number,
                "team_name": full_team_name(nickname),
                "player_name": player_name,
                "board_rank": board_rank,
            }
        )
    for player_name, share, board_rank in _JULY_2_PICK_1_SPLIT:
        rows.append(
            {
                **_JULY_2_SOURCE,
                "weight": _JULY_2_SOURCE["weight"] * share,
                "pick_number": 1,
                "team_name": full_team_name("White Sox"),
                "player_name": player_name,
                "board_rank": board_rank,
                "notes": f"{share:.0%} projection in source article",
            }
        )

    for pick_number, nickname, player_name, board_rank in _JUNE_25_PICKS:
        rows.append(
            {
                **_JUNE_25_SOURCE,
                "pick_number": pick_number,
                "team_name": full_team_name(nickname),
                "player_name": player_name,
                "board_rank": board_rank,
            }
        )

    for row in rows:
        row.setdefault("notes", None)
    return rows


def load_real_mock_draft_picks(draft_year: int = 2026) -> list[dict[str, Any]]:
    """Return normalized mock draft pick rows ready for prospect matching
    and upsert into mock_draft_picks (see mock_ingest.ingest_real_mock_draft_picks).

    This module only contains real picks transcribed from 2026 mock drafts;
    it refuses other years rather than silently relabeling 2026 data."""
    if draft_year != 2026:
        raise RuntimeError(
            f"real_mock_drafts_2026 only has picks for the 2026 draft (requested draft_year={draft_year}). "
            "Add a new module with picks for that year's real mock drafts instead of relabeling this data."
        )
    rows = _build_picks()
    for row in rows:
        row["draft_year"] = draft_year
    return rows
