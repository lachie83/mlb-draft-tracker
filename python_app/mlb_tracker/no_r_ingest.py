from __future__ import annotations

import json
import re
from html import unescape
from pathlib import Path
from typing import Any

import requests

PIPELINE_URL = 'https://www.mlb.com/milb/prospects/draft/'
NO_R_SOURCE = 'no_r_pipeline_scrape'

TOP_FALLBACK = [
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
    (11, 'Ryder Helfrick', 'C', 'Arkansas'),
    (12, 'Derek Curiel', 'OF', 'Louisiana State'),
    (13, 'Trevor Condon', 'OF', 'Etowah (GA)'),
    (14, 'Chris Hacopian', '2B', 'Texas A&M'),
    (15, 'Cameron Flukey', 'RHP', 'Coastal Carolina'),
    (16, 'Jared Grindlinger', 'LHP/OF', 'Huntington Beach (CA)'),
    (17, 'Hunter Dietz', 'LHP', 'Arkansas'),
    (18, 'Ace Reese', '3B', 'Mississippi State'),
    (19, 'AJ Gracia', 'OF', 'Virginia'),
    (20, 'Liam Peterson', 'RHP', 'Florida'),
    (21, 'Bo Lowrance', '3B', 'Christ Church Episcopal (SC)'),
    (22, 'Sawyer Strosnider', 'OF', 'Texas Christian'),
    (23, 'Brody Bumila', 'LHP', 'Bishop Feehan (MA)'),
    (24, 'Carson Bolemon', 'LHP', 'Southside Christian (SC)'),
    (25, 'Tegan Kuhns', 'RHP', 'Tennessee'),
    (26, 'Cole Carlon', 'LHP', 'Arizona State'),
    (27, 'Cole Prosek', '3B/C', 'Magnolia Heights (MS)'),
    (28, 'Daniel Jackson', 'C', 'Georgia'),
    (29, 'Aiden Robbins', 'OF', 'Texas'),
    (30, 'Zion Rose', 'OF', 'Louisville'),
    (31, 'Logan Reddemann', 'RHP', 'UCLA'),
    (32, 'Aiden Ruiz', 'SS', 'The Stony Brook (NY)'),
    (33, 'Caden Sorrell', 'OF', 'Texas A&M'),
    (34, 'Landon Thome', '2B/3B', 'Nazareth Academy (IL)'),
    (35, 'Cade Townsend', 'RHP', 'Mississippi'),
    (36, 'Mason Edwards', 'LHP', 'Southern California'),
    (37, 'Taj Marchand', 'SS', 'James Island (SC)'),
    (38, 'Gavin Grahovac', '1B', 'Texas A&M'),
    (39, 'James Clark', 'SS', 'St. John Bosco (CA)'),
    (40, 'Taylor Rabe', 'RHP', 'Mississippi'),
]


def parse_height_weight(text: str) -> tuple[str | None, int | None]:
    clean = unescape(text).strip()
    m = re.search(r'(.+?) / (\d+) lbs', clean)
    if not m:
        return clean or None, None
    return m.group(1).strip(), int(m.group(2))


def scrape_pipeline_top_rows() -> dict[int, dict[str, Any]]:
    html = requests.get(PIPELINE_URL, timeout=30).text
    pattern = re.compile(
        r'<tr[^>]*>\s*<td[^>]*rankings__table__cell--rank[^>]*>(\d+)</td>.*?<div[^>]*prospect-headshot__name[^>]*>([^<]+)</div>.*?<td[^>]*rankings__table__cell--position[^>]*>([^<]+)</td>.*?<td[^>]*rankings__table__cell--schoolName[^>]*>([^<]+)</td>.*?<td[^>]*rankings__table__cell--level[^>]*>([^<]+)</td>.*?<td[^>]*rankings__table__cell--eta[^>]*>([^<]+)</td>.*?<td[^>]*rankings__table__cell--currentAge[^>]*>([^<]+)</td>.*?<td[^>]*rankings__table__cell--heightWeight[^>]*>([^<]+)</td>.*?<td[^>]*rankings__table__cell--bats[^>]*>([^<]+)</td>.*?<td[^>]*rankings__table__cell--throws[^>]*>([^<]+)</td>',
        re.S,
    )
    out: dict[int, dict[str, Any]] = {}
    for m in pattern.finditer(html):
        rank, name, position, school, level, eta, age, hw, bats, throws = m.groups()
        height, weight = parse_height_weight(hw)
        out[int(rank)] = {
            'rank': int(rank),
            'person_full_name': unescape(name).strip(),
            'person_primary_position_name': unescape(position).strip(),
            'person_primary_position_abbreviation': unescape(position).strip(),
            'school_name': unescape(school).strip(),
            'level': unescape(level).strip(),
            'eta': unescape(eta).strip(),
            'person_current_age': int(age),
            'person_height': height,
            'person_weight': weight,
            'person_bat_side_code': None if bats == '-' else bats,
            'person_pitch_hand_code': None if throws == '-' else throws,
        }
    return out


def build_no_r_seed() -> list[dict[str, Any]]:
    scraped = scrape_pipeline_top_rows()
    rows: list[dict[str, Any]] = []
    fake_id_base = 920000
    for rank, name, position, school in TOP_FALLBACK:
        base = scraped.get(rank, {})
        row = {
            'person_id': fake_id_base + rank,
            'person_full_name': base.get('person_full_name', name),
            'person_first_name': base.get('person_full_name', name).split()[0],
            'person_last_name': base.get('person_full_name', name).split()[-1],
            'rank': rank,
            'person_primary_position_name': base.get('person_primary_position_name', position),
            'person_primary_position_abbreviation': base.get('person_primary_position_abbreviation', position),
            'school_name': base.get('school_name', school),
            'school_school_class': None,
            'person_bat_side_code': base.get('person_bat_side_code'),
            'person_pitch_hand_code': base.get('person_pitch_hand_code'),
            'is_drafted': False,
            'is_pass': False,
            'pick_round': None,
            'pick_number': None,
            'team_id': None,
            'team_name': None,
            'team_abbreviation': None,
            'headshot_link': None,
            'scouting_report': None,
            'blurb': 'No-R fallback seed from MLB Pipeline draft board.',
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
