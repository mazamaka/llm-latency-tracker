"""Orchestrator for the probe cycle. On a VPS it runs via cron (e.g. every 5 min).

    REGION=eu-west python run.py            # all providers, network + inference (if keys)
    REGION=local python run.py --only openai anthropic
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf

from _log import logger
from config import REGION, DB_PATH, providers_by_name
from db import init_db, insert, Measurement
from probe import measure_network, measure_inference


def run_once(only: list[str] | None = None, db_path: str = DB_PATH) -> list[Measurement]:
    """A single cycle: run probes across all providers in parallel, write to the DB."""
    init_db(db_path)
    providers = providers_by_name(only)
    if not providers:
        logger.warning("no providers match filter {} — cycle skipped", only)
        return []
    logger.info("probe cycle | region={} | providers={}", REGION, [p.name for p in providers])

    results: list[Measurement] = []
    with cf.ThreadPoolExecutor(max_workers=min(32, len(providers) * 2)) as ex:
        futures: list[cf.Future[Measurement | None]] = []
        for p in providers:
            futures.append(ex.submit(measure_network, p))
            futures.append(ex.submit(measure_inference, p))
        for fut in cf.as_completed(futures):
            m = fut.result()
            if m is None:                  # inference without a key returns None
                continue
            try:                           # a write error for one measurement doesn't break the rest of the cycle
                insert(db_path, m)
                results.append(m)
            except Exception as e:         # noqa: BLE001
                logger.error("insert failed for {} {}: {}", m.provider, m.probe_type, e)

    ok = sum(1 for m in results if m.status == "ok")
    logger.info("cycle done | {} measurements | {} ok", len(results), ok)
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="AI-latency probe cycle")
    ap.add_argument("--only", nargs="*", help="limit the list of providers")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()
    run_once(only=args.only, db_path=args.db)


if __name__ == "__main__":
    main()
