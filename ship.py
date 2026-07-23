"""Shipper on a probe node: sends local measurements to the central ingest endpoint.

Reliability: the probe always writes to local SQLite (data is not lost, even if the center is
unreachable). The shipper sends new rows to the center in batches by watermark (last shipped id)
and advances the watermark only after success. Center down — data piles up locally, shipped later.

stdlib-only (urllib) — a probe node doesn't need httpx.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import fields as dc_fields
from pathlib import Path

from _log import logger
from config import DB_PATH
from db import connect, Measurement

INGEST_URL = os.environ.get("INGEST_URL", "")       # e.g. https://ingest.example.com/ingest
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")
SHIP_BATCH = int(os.environ.get("SHIP_BATCH", "500"))

_COLS = [f.name for f in dc_fields(Measurement)]     # without id


def _watermark_path(db_path: str) -> Path:
    return Path(db_path + ".shipped")


def _read_watermark(db_path: str) -> int:
    p = _watermark_path(db_path)
    try:
        return int(p.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _write_watermark(db_path: str, value: int) -> None:
    _watermark_path(db_path).write_text(str(value))


def _post(url: str, token: str, rows: list[dict]) -> bool:
    body = json.dumps({"measurements": rows}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ok = 200 <= resp.status < 300
            if ok:
                logger.info("shipped {} rows → ingest ({})", len(rows), resp.status)
            else:
                logger.warning("ingest returned {}", resp.status)
            return ok
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        logger.warning("ship failed (data stays local, will be shipped later): {}", e)
        return False


def ship_pending(db_path: str = DB_PATH, url: str = INGEST_URL, token: str = INGEST_TOKEN) -> int:
    """Ship all local measurements above the watermark. Return the number shipped."""
    if not url or not token:
        logger.debug("ship: INGEST_URL/INGEST_TOKEN not set — skip (probe writes locally only)")
        return 0

    watermark = _read_watermark(db_path)
    total_shipped = 0
    while True:
        with connect(db_path) as conn:
            cur = conn.execute(
                f"SELECT id, {', '.join(_COLS)} FROM measurements WHERE id > ? ORDER BY id LIMIT ?",
                (watermark, SHIP_BATCH),
            )
            batch = [dict(r) for r in cur.fetchall()]
        if not batch:
            break
        max_id = batch[-1]["id"]
        rows = [{k: r[k] for k in _COLS} for r in batch]   # don't ship id — the center has its own autoincrement
        if not _post(url, token, rows):
            break                                          # don't advance the watermark — retry next time
        watermark = max_id
        _write_watermark(db_path, watermark)
        total_shipped += len(rows)
        if len(batch) < SHIP_BATCH:
            break
    if total_shipped:
        logger.info("ship_pending: shipped {} measurements, watermark={}", total_shipped, watermark)
    return total_shipped


def prune_shipped(db_path: str = DB_PATH) -> int:
    """Delete local measurements already shipped to the center (id <= watermark) — frees node disk."""
    wm = _read_watermark(db_path)
    if wm <= 0:
        return 0
    with connect(db_path) as conn:
        deleted = conn.execute("DELETE FROM measurements WHERE id <= ?", (wm,)).rowcount or 0
    if deleted:
        import sqlite3
        vac = sqlite3.connect(db_path)
        try:
            vac.execute("VACUUM")
        finally:
            vac.close()
        logger.info("prune_shipped: deleted {} shipped rows (id<={})", deleted, wm)
    return deleted


if __name__ == "__main__":
    ship_pending()
