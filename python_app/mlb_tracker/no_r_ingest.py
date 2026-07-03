from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path
from typing import Any

import requests

from .db import dump_json
from .sources import normalize_prospect_row

PIPELINE_URL = 'https://www.mlb.com/milb/prospects/2026/draft/'
TOP250_ARTICLE_URL = 'https://www.mlb.com/news/top-250-draft-prospects-for-2026'

TOP10_FALLBACK = [
    (1, 'Grady Emerson', 'SS', 'Fort Worth Christian (TX)'),
    (2, 'Roch Cholowsky', 'SS', 'UCLA'),
    (3, 'Vahn Lackey', 'C', 'Georgia Tech'),
    (4, 'Jackson Flora', 'RHP', 'UC Santa Barbara'),
    (5, 'Jacob Lombard', 'SS', 'Gulliver Prep (FL)'),
    (6, 'Eric Booth Jr.', 'OF', 'Oak Grove (MS)'),
    (7, 'Drew Burress', 'OF', 'Georgia Tech'),
    (8, 'Gio Rojas', 'LHP', 'Stoneman Douglas (FL)'),
    (9, 'Justin Lebron', 'SS', 'Alabama'),
    (10, 'Tyler Bell', 'SS', 'Kentucky'),
    (11, 'Chris Hacopian', '2B', 'Texas A&M'),
]


def parse_height_weight(text: str) -> tuple[str | None, int | None]:
    clean = unescape(text)
    m = re.search(r'(.+?) / (\d+) lbs', clean)
    if not m:
        return clean.strip() or None, None
    return m.group(1).strip(), int(m.group(2))


def scrape_pipeline_top5() -> list[dict[str, Any]]:
    html = requests.get(PIPELINE_URL, timeout=30).text
    pattern = re.compile(
        r'rankings__table__cell--rank">(\d+)</td><td[^>]*rankings__table__cell--player[\s\S]*?prospect-headshot__name">([^<]+)</div>[\s\S]*?rankings__table__cell--position">([^<]+)</td><td[^>]*rankings__table__cell--schoolName">([^<]+)</td>[\s\S]*?rankings__table__cell--currentAge">([^<]+)</td>[\s\S]*?rankings__table__cell--heightWeight">([^<]+)</td>',
        re.M,
    )
    out = []
    for m in pattern.finditer(html):
        rank, name, position, school, age, hw = m.groups()
        height, weight = parse_height_weight(hw)
        out.append(
            {
                'rank': int(rank),
                'person_full_name': name,
                'person_primary_position_name': position,
                'person_primary_position_abbreviation': position,
                'school_name': school,
                'person_current_age': int(age),
                'person_height': height,
                'person_weight': weight,
            }
        )
    return out


def build_no_r_seed() -> list[dict[str, Any]]:
    scraped = {row['rank']: row for row in scrape_pipeline_top5()}
    rows: list[dict[str, Any]] = []
    fake_id_base = 920000
    for rank, name, position, school in TOP10_FALLBACK:
        base = scraped.get(rank, {})
        row = {
            'person_id': base.get('person_id', fake_id_base + rank),
            'person_full_name': name,
            'person_first_name': name.split()[0],
            'person_last_name': name.split()[-1],
            'rank': rank,
            'person_primary_position_name': base.get('person_primary_position_name', position),
            'person_primary_position_abbreviation': base.get('person_primary_position_abbreviation', position),
            'school_name': base.get('school_name', school),
            'school_school_class': None,
            'person_bat_side_code': None,
            'person_pitch_hand_code': None,
            'is_drafted': False,
            'is_pass': False,
            'pick_round': None,
            'pick_number': None,
            'team_id': None,
            'team_name': None,
            'team_abbreviation': None,
            'headshot_link': None,
            'scouting_report': None,
            'blurb': 'No-R fallback seed from MLB Pipeline ranking + curated live sources.',
            'home_city': None,
            'home_state': None,
            'home_country': 'USA',
            'person_birth_date': None,
            'person_current_age': base.get('person_current_age'),
            'person_birth_city': None,
            'person_birth_state_province': None,
            'person_birth_country': None,
            'person_height': base.get('person_height'),
            'person_weight': base.get('person_weight'),
            'person_active': True,
            'draft_type_code': None,
            'draft_type_description': None,
            'bis_player_id': None,
        }
        rows.append(row)
    return rows
