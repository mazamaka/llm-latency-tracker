"""Auto-refresh the model deprecation calendar from official provider pages.

Deterministic stdlib HTML-table parser — no API key, no LLM, no external deps.
Fetches each provider's official deprecation page, finds the deprecation table(s)
by header keywords, extracts rows, normalizes dates, and merges verified entries
into data/deprecations.json. Run weekly via cron; the normal site build picks it up.

Why a parser and not an LLM: the sources are clean HTML tables, so reading the exact
cell is both free and more reliable than LLM extraction (no hallucination). Every entry
keeps its source_url so it stays verifiable. If a provider redesigns a page away from a
table, that source yields 0 rows and logs a warning (nothing is deleted).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from _log import logger

# Writable runtime path (kept out of the git repo so CD `git pull` never conflicts with cron writes).
DATA_PATH = Path(os.environ.get("DEPRECATIONS_PATH") or (Path(__file__).parent / "data" / "deprecations.json"))
_UA = "Mozilla/5.0 (compatible; llmlatency-deprecations/1.0; +https://llmlatency.dev)"

# LLM extraction (optional): a free-tier Gemini key makes extraction robust across page
# layouts. Without a key we fall back to the deterministic table parser below.
_GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
_DEP_KW = re.compile(r"deprecat|retire|shut ?down|sunset|legacy|end.of.life|discontinu|removal", re.I)

# Official deprecation pages (server-rendered tables verified 2026-07-23).
SOURCES: list[tuple[str, str]] = [
    ("OpenAI", "https://developers.openai.com/api/docs/deprecations"),
    ("Anthropic", "https://docs.anthropic.com/en/docs/about-claude/model-deprecations"),
    ("Azure OpenAI", "https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/model-retirements"),
    ("Mistral", "https://docs.mistral.ai/getting-started/models/models_overview/"),
    ("Cohere", "https://docs.cohere.com/docs/deprecations"),
]


# ─────────────────────────── HTML table extraction (stdlib) ───────────────────────────

class _TableParser(HTMLParser):
    """Collect every <table> as a list of rows; each row is a list of cell strings."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _extract_tables(html: str) -> list[list[list[str]]]:
    p = _TableParser()
    p.feed(html)
    return p.tables


# ─────────────────────────── date normalization → ISO ───────────────────────────

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_EMPTY = {"", "—", "-", "–", "n/a", "na", "tbd", "none", "no date"}


def _norm_date(s: str | None) -> str | None:
    """Parse a date in ISO / US-slash / long-month form → 'YYYY-MM-DD', else None."""
    s = (s or "").strip()
    if s.lower() in _EMPTY:
        return None
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", s)                      # 2026-04-04
    if m:
        return _safe(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", s)                      # 5/22/2026 (M/D/Y)
    if m:
        return _safe(int(m[3]), int(m[1]), int(m[2]))
    m = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b", s)       # April 30, 2025
    if m and m[1][:3].lower() in _MONTHS:
        return _safe(int(m[3]), _MONTHS[m[1][:3].lower()], int(m[2]))
    return None


def _safe(y: int, mo: int, d: int) -> str | None:
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


# ─────────────────────────── table → deprecation rows ───────────────────────────

def _col(headers: list[str], *keys: str) -> int | None:
    for i, h in enumerate(headers):
        if any(k in h for k in keys):
            return i
    return None


def _parse_table(rows: list[list[str]]) -> list[dict]:
    """Extract {model, announced, shutdown, replacement} from a deprecation-looking table."""
    if len(rows) < 2:
        return []
    headers = [h.lower() for h in rows[0]]
    i_model = _col(headers, "model", "deployment", "snapshot")
    i_shut = _col(headers, "shutdown", "shut down", "retire", "sunset", "removal", "end of life", "legacy date")
    i_ann = _col(headers, "deprecat", "announce")
    i_repl = _col(headers, "replace", "recommend", "migrat", "successor", "alternative")
    if i_model is None or (i_shut is None and i_ann is None):
        return []
    out: list[dict] = []
    for r in rows[1:]:
        if i_model >= len(r):
            continue
        model = r[i_model].strip().strip("`").strip()
        if not model or len(model) > 80 or model.lower() == "model":
            continue
        ann = _norm_date(r[i_ann]) if i_ann is not None and i_ann < len(r) else None
        shut = _norm_date(r[i_shut]) if i_shut is not None and i_shut < len(r) else None
        if not (ann or shut):                       # need at least one real date
            continue
        repl = r[i_repl].strip().strip("`").strip() if i_repl is not None and i_repl < len(r) else ""
        out.append({"model": model, "announced": ann, "shutdown": shut,
                    "replacement": repl or None})
    return out


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=25) as resp:      # noqa: S310 - fixed provider allowlist
        return resp.read().decode("utf-8", "replace")


def _tables_text(html: str) -> str:
    """Serialize only deprecation-relevant tables to compact pipe-delimited text (small LLM input)."""
    chunks = []
    for t in _extract_tables(html):
        txt = "\n".join(" | ".join(row) for row in t)
        if _DEP_KW.search(txt):
            chunks.append(txt)
    return "\n\n---\n\n".join(chunks)


def _page_text(html: str) -> str:
    """Strip HTML → text (catches deprecation data that isn't inside a <table>, e.g. div-rendered docs)."""
    html = re.sub(r"(?is)<(script|style|svg|noscript|head)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&(amp|nbsp|lt|gt|quot|#39|apos);", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n\s*", "\n", text).strip()


def _gemini(prompt: str) -> str:
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }).encode()
    req = urllib.request.Request(
        _GEMINI_URL.format(_GEMINI_MODEL), data=body, method="POST",
        headers={"x-goog-api-key": _GEMINI_KEY, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:      # noqa: S310
        d = json.load(resp)
    return d["candidates"][0]["content"]["parts"][0]["text"]


_PROMPT = (
    "From the tables below (from {provider}'s official model-deprecation page), extract every model "
    "deprecation/retirement entry. Return ONLY a JSON array. Each item: "
    '{{"model": string, "announced": "YYYY-MM-DD" or null, "shutdown": "YYYY-MM-DD" or null '
    '(the retirement/shutdown date), "replacement": string or null}}. Rules: include only rows with at '
    "least one real date; normalize every date to YYYY-MM-DD; use the primary model id if a cell lists "
    "aliases; never invent models or dates. If nothing qualifies, return [].\n\nTABLES:\n{tables}"
)


def _llm_rows(provider: str, tables_text: str) -> list[dict]:
    raw = _gemini(_PROMPT.format(provider=provider, tables=tables_text[:300000]))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", raw, re.S)
        data = json.loads(m.group(0)) if m else []
    out: list[dict] = []
    for e in data if isinstance(data, list) else []:
        model = str(e.get("model", "")).strip().strip("`")
        ann, shut = _norm_date(e.get("announced")), _norm_date(e.get("shutdown"))
        if model and (ann or shut):
            repl = str(e["replacement"]).strip() if e.get("replacement") else ""
            out.append({"model": model, "announced": ann, "shutdown": shut, "replacement": repl or None})
    return out


def fetch_all() -> list[dict]:
    """Fetch every source and extract entries (LLM if a Gemini key is set, else the table parser)."""
    entries: list[dict] = []
    for provider, url in SOURCES:
        try:
            html = _fetch(url)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            logger.warning("deprecations: fetch failed for {} ({}) — skip", provider, e)
            continue
        found: list[dict] = []
        if _GEMINI_KEY:
            # tables first (compact, guaranteed within the cap) + full page text (catches div-rendered data)
            content = _tables_text(html) + "\n\n=== PAGE ===\n\n" + _page_text(html)
            try:
                found = _llm_rows(provider, content)
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as e:
                logger.warning("deprecations: LLM extract failed for {} ({}) — using parser", provider, e)
        if not found:                                   # no key, or LLM failed → deterministic parser
            for table in _extract_tables(html):
                found += _parse_table(table)
        if not found:
            logger.warning("deprecations: 0 entries for {} — page may have changed: {}", provider, url)
            continue
        for e in found:
            e.update(provider=provider, source_url=url, verified=True)
        logger.info("deprecations: {} entries from {}", len(found), provider)
        entries += found
    return entries


# ─────────────────────────── merge into data/deprecations.json ───────────────────────────

def _key(e: dict) -> tuple[str, str]:
    return (e["provider"].lower().strip(), re.sub(r"\s+", " ", e["model"].lower()).strip().strip("`"))


def merge(new: list[dict], path: Path = DATA_PATH) -> tuple[int, int]:
    """The fetched providers are authoritative (same official pages): replace their entries with the
    fresh set, keep entries from any other provider. Dedup the fresh set by (provider, model)."""
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"entries": [], "_sources": {}}
    fetched = {p.lower() for p, _ in SOURCES}
    kept = [e for e in raw.get("entries", []) if str(e.get("provider", "")).lower() not in fetched]
    fresh: dict[tuple[str, str], dict] = {}
    for e in new:                                    # dedup by (provider, model); last wins
        fresh[_key(e)] = e
    raw["entries"] = kept + list(fresh.values())
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(fresh), len(kept)


def run(path: Path = DATA_PATH) -> tuple[int, int]:
    entries = fetch_all()
    if not entries:
        logger.warning("deprecations: nothing fetched — calendar left unchanged")
        return (0, 0)
    published, kept = merge(entries, path)
    logger.info("deprecations refresh: {} entries from {} sources, {} other kept", published, len(SOURCES), kept)
    return (published, kept)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Refresh the deprecation calendar from official pages")
    ap.add_argument("--dry-run", action="store_true", help="parse + print, do not write")
    args = ap.parse_args()
    if args.dry_run:
        for e in fetch_all():
            print(f"  [{e['provider']:12}] {e['model']:32} dep={e['announced']} shut={e['shutdown']} → {e['replacement']}")
    else:
        run()
