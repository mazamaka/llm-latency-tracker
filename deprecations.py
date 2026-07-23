"""Model deprecation/migration calendar — a freshness magnet with high-intent queries.

Answers "when is [model] being removed" / "what to replace [model] with" — these are high-intent
queries (the user is looking for an action → migration → provider switch → OpenRouter referral).

IMPORTANT: this is public content. Entries without verified=true and without source_url are NOT
presented as fact — they render with an "unverified" mark. Dates are taken ONLY from providers'
official deprecation pages (see data/deprecations.json → _sources). The agent fills them in from there.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from _log import logger

import os as _os  # noqa: E402
# Writable runtime path (set on servers to a data volume so the weekly fetcher's writes
# live outside the git checkout and never conflict with the CD `git pull`).
DATA_PATH = Path(_os.environ.get("DEPRECATIONS_PATH") or (Path(__file__).parent / "data" / "deprecations.json"))


@dataclass
class Deprecation:
    provider: str
    model: str
    announced: str | None
    shutdown: str | None
    replacement: str | None
    source_url: str
    verified: bool
    note: str | None = None

    @property
    def is_past(self) -> bool:
        if not self.shutdown:
            return False
        try:
            return date.fromisoformat(self.shutdown) < date.today()
        except ValueError:
            return False


def load(path: Path = DATA_PATH) -> list[Deprecation]:
    """Load calendar entries. Returns [] if the file is missing — the page just isn't built."""
    if not path.exists():
        logger.warning("deprecations data not found at {}", path)
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for e in raw.get("entries", []):
        try:
            out.append(Deprecation(
                provider=e["provider"], model=e["model"], announced=e.get("announced"),
                shutdown=e.get("shutdown"), replacement=e.get("replacement"),
                source_url=e["source_url"], verified=bool(e.get("verified", False)),
                note=e.get("note"),
            ))
        except KeyError as k:
            logger.warning("deprecation entry missing field {}, skip: {}", k, e)
    logger.debug("loaded {} deprecation entries ({} verified)",
                 len(out), sum(1 for d in out if d.verified))
    return out


def upcoming(entries: list[Deprecation]) -> list[Deprecation]:
    """Only future/open-ended entries, sorted by shutdown date (verified first)."""
    future = [d for d in entries if not d.is_past]
    return sorted(future, key=lambda d: (not d.verified, d.shutdown or "9999"))


def recent(entries: list[Deprecation], limit: int = 12) -> list[Deprecation]:
    """Recently shut down (past), newest first — for "what to replace X with" queries."""
    past = [d for d in entries if d.is_past and d.verified]
    return sorted(past, key=lambda d: d.shutdown or "", reverse=True)[:limit]


def sources(path: Path = DATA_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("_sources", {})
