# Deploy

Two shapes: a single all-in-one node (quick), or a multi-region setup (a central node + remote probe nodes).

## Single node (all-in-one)

One node measures, builds the site, and prunes. Good enough to get a live site with one region.

```bash
git clone https://github.com/mazamaka/llm-latency-tracker /opt/llm-latency-tracker
cd /opt/llm-latency-tracker
cp .env.example .env          # set REGION (e.g. eu-west), MODE=all, BASE_URL
docker compose -f deploy/docker-compose.yml up -d --build
```

`MODE=all` runs the probe cycle, builds the static site into the mounted volume, and prunes old rows on a schedule. Serve the generated `site/` directory from any static host (e.g. Cloudflare Pages, Vercel, Netlify), or point your own web server at it.

Resource limits in `docker-compose.yml` keep the probe container small (0.25 CPU / 128 MB / no inbound ports), so it can safely share a box with other services.

## Multi-region

Remote probe nodes measure their own region and **ship** measurements to a central node that aggregates and builds the site. Probe nodes write locally first, so no data is lost if the central node is briefly unavailable.

### 1. Central node — ingest endpoint

Run `ingest.py` (already wired as a service in a compose/stack of your choice). Put it behind a reverse proxy with HTTPS and a domain (e.g. `ingest.example.com`), and set a strong bearer token:

```bash
INGEST_TOKEN=$(openssl rand -hex 32)   # share this with every probe node
```

Health check: `curl https://ingest.example.com/health` → `{"ok":true}`.

### 2. Each probe node

```bash
git clone https://github.com/mazamaka/llm-latency-tracker && cd llm-latency-tracker
export REGION=us-east                              # ap-tokyo / sa-east / ...
export INGEST_URL=https://ingest.example.com/ingest
export INGEST_TOKEN=<the same token as the central node>
docker compose -f deploy/docker-compose.yml up -d --build   # MODE=probe is the default here
```

The node measures its region and ships to the central node; on an outage, data queues locally and is shipped later (watermark-based, so nothing is double-counted or lost).

### 3. Site build & publish

On the central node (or in CI), build the site and publish `site/` to your static host:

```bash
BASE_URL=https://your-domain python3 sitegen.py    # → ./site/
# then publish ./site to your static host of choice
```

Rebuild on a schedule (cron every 15–30 min) so pages stay fresh.

## Notes

- **Retention:** `RETENTION_DAYS` (default 90) bounds disk on the central node; raw rows older than that are pruned and space reclaimed via `VACUUM`.
- **No inbound ports on probe nodes** — they only make outbound requests.
- **Secrets** live only in `.env` (gitignored). Never commit tokens or API keys.
