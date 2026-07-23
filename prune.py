"""Retention: keep the DB bounded so it never fills the central node's disk.

The central node may also run other workloads, so keep the DB bounded.
Raw samples older than RETENTION_DAYS are deleted; space is reclaimed via VACUUM.
Long-term trends can later be stored as compact daily roll-ups rather than raw rows.
"""
from __future__ import annotations

import sqlite3

from _log import logger
from config import DB_PATH, RETENTION_DAYS
from db import connect


def prune(db_path: str = DB_PATH, days: int = RETENTION_DAYS) -> int:
    """Delete measurements (and citations) older than `days`. Return the number of deleted rows. Then VACUUM."""
    cutoff = f"-{int(days)} days"
    deleted = 0
    with connect(db_path) as conn:
        cur = conn.execute("DELETE FROM measurements WHERE ts < datetime('now', ?)", (cutoff,))
        deleted = cur.rowcount or 0
        # the citations table may not exist if citation_monitor was never run
        try:
            conn.execute("DELETE FROM citations WHERE ts < datetime('now', ?)", (cutoff,))
        except sqlite3.OperationalError:
            pass
    # the commit happened on exit from the with; now VACUUM in a separate autocommit connection
    # (must run outside a transaction) — rebuilds the file, returns space to the OS and checkpoints WAL
    vac = sqlite3.connect(db_path)
    try:
        vac.execute("VACUUM")
    finally:
        vac.close()

    logger.info("pruned {} rows older than {}d, db vacuumed", deleted, days)
    return deleted


if __name__ == "__main__":
    prune()
