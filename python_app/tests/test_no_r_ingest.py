from __future__ import annotations

from mlb_tracker import no_r_ingest
from mlb_tracker.no_r_ingest import TOP_FALLBACK, build_no_r_seed, parse_height_weight


def test_parse_height_weight_extracts_height_and_pounds():
    height, weight = parse_height_weight("6-2 / 190 lbs")
    assert height == "6-2"
    assert weight == 190


def test_parse_height_weight_handles_missing_weight():
    height, weight = parse_height_weight("-")
    assert height == "-"
    assert weight is None


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


def _fake_row_html(rank: int, name: str, position: str, school: str, age: int, height_weight: str) -> str:
    return f"""
    <tr>
      <td class="rankings__table__cell--rank">{rank}</td>
      <td class="rankings__table__cell--player">
        <div class="prospect-headshot__name">{name}</div>
      </td>
      <td class="rankings__table__cell--position">{position}</td>
      <td class="rankings__table__cell--schoolName">{school}</td>
      <td class="rankings__table__cell--level">NCAA</td>
      <td class="rankings__table__cell--eta">2026</td>
      <td class="rankings__table__cell--currentAge">{age}</td>
      <td class="rankings__table__cell--heightWeight">{height_weight}</td>
      <td class="rankings__table__cell--bats">R</td>
      <td class="rankings__table__cell--throws">R</td>
    </tr>
    """


def test_build_no_r_seed_merges_scraped_rows_with_curated_fallback(monkeypatch):
    fake_html = _fake_row_html(1, "Scraped Player One", "3B", "Scraped University", 20, "6-2 / 190 lbs")
    monkeypatch.setattr(no_r_ingest.requests, "get", lambda *a, **k: _FakeResponse(fake_html))

    rows = build_no_r_seed()

    assert len(rows) == len(TOP_FALLBACK)
    by_rank = {row["rank"]: row for row in rows}

    scraped_row = by_rank[1]
    assert scraped_row["person_full_name"] == "Scraped Player One"
    assert scraped_row["school_name"] == "Scraped University"
    assert scraped_row["person_height"] == "6-2"
    assert scraped_row["person_weight"] == 190

    curated_rank, curated_name, curated_position, curated_school = TOP_FALLBACK[1]
    fallback_row = by_rank[curated_rank]
    assert fallback_row["person_full_name"] == curated_name
    assert fallback_row["school_name"] == curated_school
    assert fallback_row["person_height"] is None


def test_build_no_r_seed_all_rows_undrafted_and_unranked_pick(monkeypatch):
    monkeypatch.setattr(no_r_ingest.requests, "get", lambda *a, **k: _FakeResponse(""))

    rows = build_no_r_seed()

    assert all(row["is_drafted"] is False for row in rows)
    assert all(row["pick_number"] is None for row in rows)
