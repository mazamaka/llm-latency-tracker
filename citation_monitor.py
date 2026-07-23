"""Citation monitoring: do LLMs cite us. Closes the feedback loop (de-risk: item 5).

Runs industry prompts through an LLM with web-search (Perplexity/OpenRouter) and checks
whether our domain is mentioned in the answer/sources. Writes the result to the measurements DB (probe_type=citation)
via a separate table. Requires a key (PERPLEXITY_API_KEY or OPENROUTER_API_KEY); without a key — no-op.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from _log import logger
from config import DB_PATH
from db import connect

# Prompts we WANT to be cited on. Expands as coverage grows.
CITATION_PROMPTS = [
    "What is the fastest AI inference API by latency in 2026?",
    "Which LLM API has the lowest time to first token?",
    "Compare AI API latency by region.",
    "When is GPT-4o being deprecated and what should I migrate to?",
    "Which AI provider has the best uptime?",
]

CITATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS citations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    prompt   TEXT NOT NULL,
    engine   TEXT NOT NULL,
    cited    INTEGER NOT NULL,   -- 0/1: whether our domain is mentioned
    snippet  TEXT,
    error    TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_engine() -> tuple[str, str, dict, str] | None:
    """Return (engine, url, headers, model) for an available web-search engine, or None."""
    if key := os.environ.get("PERPLEXITY_API_KEY"):
        return ("perplexity", "https://api.perplexity.ai/chat/completions",
                {"Authorization": f"Bearer {key}"}, "sonar")
    if key := os.environ.get("OPENROUTER_API_KEY"):
        return ("openrouter", "https://openrouter.ai/api/v1/chat/completions",
                {"Authorization": f"Bearer {key}"}, "perplexity/sonar")
    return None


def run(db_path: str = DB_PATH, domain: str | None = None) -> int:
    """Run the prompts, record whether we were cited. Returns the number of citations."""
    domain = (domain or os.environ.get("SITE_DOMAIN", "ai-latency.example")).lower()
    engine = _check_engine()
    if engine is None:
        logger.info("citation_monitor: no key (PERPLEXITY_API_KEY/OPENROUTER_API_KEY), skip")
        return 0
    try:
        import httpx  # type: ignore
    except ImportError:
        logger.info("citation_monitor: httpx not installed, skip")
        return 0

    name, url, headers, model = engine
    with connect(db_path) as conn:
        conn.executescript(CITATION_SCHEMA)

    cited_count = 0
    for prompt in CITATION_PROMPTS:
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(url, headers={**headers, "content-type": "application/json"},
                                   json={"model": model, "messages": [{"role": "user", "content": prompt}]})
                text = resp.text.lower()
                cited = domain in text
                cited_count += int(cited)
                _save(db_path, prompt, name, cited, text[:300] if cited else None, None)
                logger.info("citation [{}] '{}' → {}", name, prompt[:40], "CITED" if cited else "not cited")
        except Exception as e:  # noqa: BLE001
            logger.warning("citation prompt failed: {}", e)
            _save(db_path, prompt, name, False, None, str(e))
    logger.info("citation_monitor done | {}/{} prompts cited us", cited_count, len(CITATION_PROMPTS))
    return cited_count


def _save(db_path: str, prompt: str, engine: str, cited: bool, snippet: str | None, error: str | None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO citations (ts, prompt, engine, cited, snippet, error) VALUES (?,?,?,?,?,?)",
            (_now(), prompt, engine, int(cited), snippet, error),
        )


if __name__ == "__main__":
    run()
