"""Continuous probe loop + site rebuild. For local runs or inside a container.

    REGION=eu-west INTERVAL=300 python3 schedule.py

In production prefer a systemd timer/cron instead (see deploy/) — more robust than a long-running process.
"""
from __future__ import annotations

import os
import time

from _log import logger
from run import run_once
from sitegen import build
from prune import prune
from ship import ship_pending, prune_shipped

INTERVAL = int(os.environ.get("INTERVAL", "300"))          # seconds between cycles
REBUILD_EVERY = int(os.environ.get("REBUILD_EVERY", "1"))  # rebuild the site once every N cycles
SITE_DIR = os.environ.get("SITE_DIR", "site")              # where to build the site (in a container — /data/site, writable)
# MODE=probe — measurements only (remote node); MODE=all — measurements+build+cleanup (central/single-node)
MODE = os.environ.get("MODE", "all").lower()
_PRUNE_EVERY = max(1, 86400 // INTERVAL)                   # cleanup ~once a day


def main() -> None:
    logger.info("scheduler start | mode={} | interval={}s | rebuild_every={}", MODE, INTERVAL, REBUILD_EVERY)
    cycle = 0
    while True:
        cycle += 1
        try:
            results = run_once()
            if MODE == "probe":
                ship_pending()                             # ship local measurements to the center (if INGEST_URL is set)
                if cycle % _PRUNE_EVERY == 0:
                    prune_shipped()                        # delete what's already shipped — node disk doesn't grow
            else:
                if cycle % REBUILD_EVERY == 0:
                    build(out_dir=SITE_DIR)
                if cycle % _PRUNE_EVERY == 0:
                    prune()                                # keep disk bounded
            logger.info("cycle {} ok | {} measurements", cycle, len(results))
        except Exception as e:  # noqa: BLE001 - the scheduler must not crash on a single error
            logger.error("cycle {} failed: {}", cycle, e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
