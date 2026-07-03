from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import requests
from bs4 import BeautifulSoup

MLB_PROSPECTS_ARTICLE = "https://www.mlb.com/news/top-250-draft-prospects-for-2026"
MLB_DRAFT_ORDER_URL = "https://www.mlb.com/draft/2026/order"
BASEBALLR_DOC_URL = "https://billpetti.github.io/baseballr/reference/mlb_draft_prospects.html"


@dataclass
class HttpClient:
    timeout: int = 30

    def get_text(self, url: str) -> str:
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.text

    def get_json(self, url: str) -> Any:
        response = requests.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.json()


def extract_next_data(html: str) -> dict[str, Any]:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("Could not find __NEXT_DATA__")
    return json.loads(m.group(1))


def extract_initial_data(html: str) -> dict[str, Any]:
    m = re.search(r'<script id="initial-data" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("Could not find initial-data")
    return json.loads(m.group(1))


def parse_top10_from_article(html: str) -> list[dict[str, Any]]:
    data = extract_initial_data(html)
    blob = json.dumps(data)
    m = re.search(r'1\\\. <a href=\"[^\"]+\">([^<]+)</a>, ([^,]+), ([^\\n]+).*?2\\\. <a href=\"[^\"]+\">([^<]+)</a>, ([^,]+), ([^\\n]+)', blob)
    if not m:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n")
        return [{"raw_text": text[:2000]}]
    return []


def fetch_mlb_draft_order_page(client: HttpClient | None = None) -> dict[str, Any]:
    client = client or HttpClient()
    html = client.get_text(MLB_DRAFT_ORDER_URL)
    return extract_next_data(html)


def fetch_mlb_top250_article(client: HttpClient | None = None) -> dict[str, Any]:
    client = client or HttpClient()
    html = client.get_text(MLB_PROSPECTS_ARTICLE)
    return extract_initial_data(html)


def fetch_baseballr_doc_text(client: HttpClient | None = None) -> str:
    client = client or HttpClient()
    html = client.get_text(BASEBALLR_DOC_URL)
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text("\n")
