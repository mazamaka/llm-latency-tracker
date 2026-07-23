"""Time-series store for measurements (SQLite for the skeleton; Postgres/Timescale later).

The schema is the heart of the product: it's the proprietary archive that can't be copied.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from collections.abc import Iterator

from _log import logger

# Identifiers that end up in file paths (region/provider.html) and in HTML/JSON-LD.
# We validate at the write layer — this is the canonical fix: it closes both path-traversal
# and HTML/JSON-LD injection at once when merging the DB from several regional nodes (see code review).
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class InvalidIdentifier(ValueError):
    """region/provider/probe_type failed the allow-list — the write is rejected."""


def _validate_id(field: str, value: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID.match(value):
        raise InvalidIdentifier(f"{field}={value!r} does not match ^[a-z0-9][a-z0-9_-]*$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS measurements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,        -- ISO-8601 UTC
    provider    TEXT    NOT NULL,        -- openai, anthropic, ...
    model       TEXT,                    -- NULL for the network probe
    region      TEXT    NOT NULL,        -- eu-west, us-east, ap-tokyo, local
    probe_type  TEXT    NOT NULL,        -- network | inference
    dns_ms      REAL,
    connect_ms  REAL,                    -- TCP handshake
    tls_ms      REAL,                    -- TLS handshake
    ttfb_ms     REAL,                    -- time to first byte (network)
    ttft_ms     REAL,                    -- time to first token (inference)
    total_ms    REAL,
    status      TEXT    NOT NULL,        -- ok | error | timeout
    http_status INTEGER,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_meas_lookup ON measurements (provider, region, probe_type, ts);
"""


@dataclass
class Measurement:
    ts: str
    provider: str
    region: str
    probe_type: str                 # network | inference
    status: str                     # ok | error | timeout
    model: str | None = None
    dns_ms: float | None = None
    connect_ms: float | None = None
    tls_ms: float | None = None
    ttfb_ms: float | None = None
    ttft_ms: float | None = None
    total_ms: float | None = None
    http_status: int | None = None
    error: str | None = None


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    # timeout=30: a cushion against 'database is locked' under concurrent access
    # (schedule.py writes, sitegen/citation_monitor read the same file).
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL: readers aren't blocked by the writer (several processes on one file).
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    """Create the schema if it doesn't exist."""
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
    logger.debug("db initialised at {}", db_path)


# Explicit column list — independent of dict key order and doesn't confuse SQL scanners.
_COLUMNS = ("ts", "provider", "model", "region", "probe_type", "dns_ms", "connect_ms",
            "tls_ms", "ttfb_ms", "ttft_ms", "total_ms", "status", "http_status", "error")
_INSERT_SQL = (f"INSERT INTO measurements ({', '.join(_COLUMNS)}) "
               f"VALUES ({', '.join(':' + c for c in _COLUMNS)})")


def insert(db_path: str, m: Measurement) -> None:
    """Write a single measurement. Identifiers are validated (allow-list) before the write."""
    _validate_id("provider", m.provider)
    _validate_id("region", m.region)
    _validate_id("probe_type", m.probe_type)
    with connect(db_path) as conn:
        conn.execute(_INSERT_SQL, asdict(m))
    logger.debug("saved {} {} {} status={}", m.provider, m.probe_type, m.region, m.status)


def insert_many(db_path: str, ms: list[Measurement]) -> None:
    for m in ms:
        insert(db_path, m)
