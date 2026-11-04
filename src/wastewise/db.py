"""File-backed SQLite storage. Each request receives its own connection."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS bins (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    location TEXT NOT NULL,
    latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    waste_type TEXT NOT NULL CHECK (waste_type IN ('general', 'recycling', 'organic')),
    capacity_litres REAL NOT NULL CHECK (capacity_litres > 0),
    fill_percent REAL NOT NULL CHECK (fill_percent BETWEEN 0 AND 100),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pickup_requests (
    id INTEGER PRIMARY KEY,
    bin_id INTEGER NOT NULL REFERENCES bins(id),
    priority TEXT NOT NULL CHECK (priority IN ('normal', 'urgent')),
    notes TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'cancelled')),
    created_at TEXT NOT NULL,
    closed_at TEXT,
    CHECK ((status = 'pending' AND closed_at IS NULL)
        OR (status != 'pending' AND closed_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_request_per_bin
ON pickup_requests (bin_id) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY,
    bin_id INTEGER NOT NULL REFERENCES bins(id),
    collected_at TEXT NOT NULL,
    estimated_litres REAL NOT NULL CHECK (estimated_litres >= 0)
);
"""


@contextmanager
def connect(path: Path):
    connection = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
