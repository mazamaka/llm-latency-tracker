"""Aggregates measurements into what gets rendered onto the site's pages.

Shows how a time series turns into content like
"fastest AI API from Europe" / "[provider] reliability" — the actual numbers
that ChatGPT/Perplexity cite. These are demo queries; the site generator takes
their output and renders static pages + llms.txt + schema.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from _log import logger
from config import DB_PATH
from db import connect


@dataclass
class Ranking:
    provider: str
    samples: int
    p50_ms: float
    p95_ms: float
    ok_rate: float


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile with deterministic round-half-up (not banker's rounding via round()),
    since p50/p95 are the entire published content, rounding at the .5 boundary must be predictable."""
    if not values:
        return 0.0
    values = sorted(values)
    import math
    idx = math.floor((pct / 100) * (len(values) - 1) + 0.5)
    k = max(0, min(len(values) - 1, idx))
    return round(values[k], 1)


def ttfb_ranking(db_path: str, region: str, hours: int = 24, probe_type: str = "network") -> list[Ranking]:
    """Provider ranking by TTFB/TTFT in a region over the last `hours` hours."""
    metric = "ttft_ms" if probe_type == "inference" else "ttfb_ms"
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT provider, {metric} AS val, status
            FROM measurements
            WHERE region = ? AND probe_type = ?
              AND ts >= datetime('now', ?)
            """,
            (region, probe_type, f"-{hours} hours"),
        ).fetchall()

    by_provider: dict[str, list[tuple[float | None, str]]] = {}
    for r in rows:
        by_provider.setdefault(r["provider"], []).append((r["val"], r["status"]))

    out: list[Ranking] = []
    for provider, samples in by_provider.items():
        vals = [v for v, s in samples if v is not None and s == "ok"]
        ok_rate = sum(1 for _, s in samples if s == "ok") / len(samples) if samples else 0.0
        out.append(Ranking(
            provider=provider, samples=len(samples),
            p50_ms=_percentile(vals, 50), p95_ms=_percentile(vals, 95),
            ok_rate=round(ok_rate * 100, 1),
        ))
    out.sort(key=lambda r: (r.p50_ms if r.p50_ms else 1e9))
    return out


def latency_series(db_path: str, provider: str, region: str, hours: int = 72,
                   probe_type: str = "network") -> list[tuple[str, float]]:
    """Hourly p50 TTFB/TTFT for a provider in a region over the last `hours`. For the inline chart."""
    metric = "ttft_ms" if probe_type == "inference" else "ttfb_ms"
    with connect(db_path) as conn:
        rows = conn.execute(
            f"""SELECT strftime('%Y-%m-%dT%H:00', ts) AS hr, {metric} AS val
                FROM measurements
                WHERE provider=? AND region=? AND probe_type=? AND status='ok' AND {metric} IS NOT NULL
                  AND ts >= datetime('now', ?)
                ORDER BY hr""",
            (provider, region, probe_type, f"-{hours} hours"),
        ).fetchall()
    buckets: dict[str, list[float]] = {}
    for r in rows:
        buckets.setdefault(r["hr"], []).append(r["val"])
    return [(hr, _percentile(vals, 50)) for hr, vals in sorted(buckets.items())]


def render_console(region: str, ranking: list[Ranking], probe_type: str) -> None:
    metric = "TTFT" if probe_type == "inference" else "TTFB (edge)"
    title = f"Fastest AI API from '{region}' — by {metric}, last 24h"
    print("\n" + title)
    print("=" * len(title))
    print(f"{'#':<3}{'provider':<14}{'p50':>9}{'p95':>9}{'uptime':>9}{'n':>6}")
    print("-" * 50)
    for i, r in enumerate(ranking, 1):
        print(f"{i:<3}{r.provider:<14}{r.p50_ms:>7.0f}ms{r.p95_ms:>7.0f}ms{r.ok_rate:>8.0f}%{r.samples:>6}")
    if not ranking:
        print("(no data — run first: python run.py)")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Aggregate measurements into page rankings")
    ap.add_argument("--region", default="local")
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--type", default="network", choices=["network", "inference"])
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()
    ranking = ttfb_ranking(args.db, args.region, args.hours, args.type)
    logger.debug("ranking: {} providers", len(ranking))
    render_console(args.region, ranking, args.type)


if __name__ == "__main__":
    main()
