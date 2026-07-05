from __future__ import annotations

import sqlite3

import pytest

from mlb_tracker.db import get_connection, init_db


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    init_db(path)
    return path


@pytest.fixture
def conn(db_path) -> sqlite3.Connection:
    connection = get_connection(db_path)
    yield connection
    connection.close()
