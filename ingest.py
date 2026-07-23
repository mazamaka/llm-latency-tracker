"""Ingest endpoint on the central node: receives measurements from remote probe nodes.

Sits behind a reverse proxy (HTTPS + domain, e.g. ingest.example.com).
Authentication is a bearer token (INGEST_TOKEN). Every measurement is validated via db.insert
(allow-list of identifiers → no injection/traversal). The body is size-limited.

Run: INGEST_TOKEN=... INGEST_PORT=8787 DB_PATH=/data/measurements.db python3 ingest.py
"""
from __future__ import annotations

import hmac
import json
import os
from dataclasses import fields as dc_fields
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from _log import logger
from config import DB_PATH
from db import init_db, insert, Measurement, InvalidIdentifier

INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")
INGEST_PORT = int(os.environ.get("INGEST_PORT", "8787"))
MAX_BODY = int(os.environ.get("INGEST_MAX_BODY", str(2 * 1024 * 1024)))  # 2 MB
MAX_BATCH = int(os.environ.get("INGEST_MAX_BATCH", "5000"))

_ALLOWED = {f.name for f in dc_fields(Measurement)}
_REQUIRED = {"ts", "provider", "region", "probe_type", "status"}


def _measurement_from(d: dict) -> Measurement:
    """Build a Measurement from known fields only (ignore anything extra)."""
    if not _REQUIRED.issubset(d):
        raise ValueError(f"missing required fields: {_REQUIRED - set(d)}")
    clean = {k: v for k, v in d.items() if k in _ALLOWED}
    return Measurement(**clean)


class _Handler(BaseHTTPRequestHandler):
    server_version = "ai-latency-ingest/1.0"

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not INGEST_TOKEN:                       # unset token = deny everything (fail-closed)
            return False
        auth = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not auth.startswith(prefix):
            return False
        return hmac.compare_digest(auth[len(prefix):], INGEST_TOKEN)  # constant-time

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/ingest":
            return self._json(404, {"error": "not found"})
        if not self._authorized():
            return self._json(401, {"error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._json(400, {"error": "bad content-length"})
        if length <= 0 or length > MAX_BODY:
            return self._json(413, {"error": "body too large or empty"})

        try:
            payload = json.loads(self.rfile.read(length))
            rows = payload["measurements"]
            if not isinstance(rows, list):
                raise ValueError("measurements must be a list")
            if len(rows) > MAX_BATCH:
                return self._json(413, {"error": f"batch > {MAX_BATCH}"})
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return self._json(400, {"error": f"bad payload: {e}"})

        inserted, rejected = 0, 0
        for d in rows:
            try:
                insert(self.db_path, _measurement_from(d))
                inserted += 1
            except (InvalidIdentifier, ValueError, TypeError) as e:
                rejected += 1
                logger.warning("ingest rejected row: {}", e)
        logger.info("ingest: {} inserted, {} rejected", inserted, rejected)
        self._json(200, {"inserted": inserted, "rejected": rejected})

    def log_message(self, *_a) -> None:  # silence the default per-request stderr log
        pass


def serve(db_path: str = DB_PATH, port: int = INGEST_PORT) -> None:
    if not INGEST_TOKEN:
        raise SystemExit("INGEST_TOKEN not set — refusing to start (fail-closed)")
    init_db(db_path)
    _Handler.db_path = db_path  # type: ignore[attr-defined]
    httpd = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    logger.info("ingest listening on :{} (behind your NPM/HTTPS)", port)
    httpd.serve_forever()


if __name__ == "__main__":
    serve()
