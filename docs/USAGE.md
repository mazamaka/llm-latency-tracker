# 🧰 Usage & measurement pipeline

## For developers

```bash
# All regions, provider rankings for the last 24h — measured latency + uptime:
curl https://llmlatency.dev/api/rankings.json
```

- **JSON API:** [`/api/rankings.json`](https://llmlatency.dev/api/rankings.json) · **OpenAPI:** [`/openapi.json`](https://llmlatency.dev/openapi.json)
- **Any page as Markdown:** send `Accept: text/markdown` to any page URL, or append `.md`.
- **For LLM ingestion:** [`/llms.txt`](https://llmlatency.dev/llms.txt) (index) and [`/llms-full.txt`](https://llmlatency.dev/llms-full.txt) (full corpus).
- **License:** data is **CC-BY-4.0** — free to use with attribution.

## For AI agents

There's a real **MCP server** (Streamable HTTP) exposing a `get_ai_api_latency` tool backed by the live data:

```bash
curl -X POST https://llmlatency.dev/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"get_ai_api_latency","arguments":{"region":"eu-hetzner"}}}'
```

### Run the MCP server locally

The hosted endpoint above needs no setup. If you prefer a local stdio server (or want to build it from source), `mcp_server.py` is a dependency-free proxy over the same public JSON API. It exposes four read-only tools:

- `get_ai_api_latency` — provider leaderboard (TTFB p50/p95, uptime) for one region or all regions
- `get_provider_latency` — one provider's rank and latency in every region
- `get_model_deprecations` — announced model shutdowns, replacements and provider notice periods
- `get_coding_agent_health` — Claude Code / Codex speed, thinking tokens, TTFT and accuracy vs their own baseline

```bash
python3 mcp_server.py            # stdio MCP, stdlib only
# or
docker build -t llm-latency-mcp . && docker run -i llm-latency-mcp
```

Also available: an [MCP Server Card](https://llmlatency.dev/.well-known/mcp/server-card.json) (`/.well-known/mcp/server-card.json`), a browser **WebMCP** tool, an [API catalog](https://llmlatency.dev/.well-known/api-catalog) (RFC 9727) and an [Agent Skills index](https://llmlatency.dev/.well-known/agent-skills/index.json). Regions: `eu-hetzner`, `us-central`, `ap-tokyo`, `sa-east` (omit for all).

## How it works

```
config.py       — registry of providers + this node's REGION (env)
probe.py        — network probe (DNS→TCP→TLS→TTFB, stdlib, no key) + inference probe (TTFT, needs key)
run.py          — one probe cycle across all providers (run on a schedule)
db.py           — SQLite time-series (the accumulated measurement archive)
aggregate.py    — measurements → p50 / p95 / uptime rankings per region & provider
sitegen.py      — rankings → static site (JSON API, OpenAPI, llms.txt, schema.org, MCP surface)
ingest.py       — central endpoint that collects measurements from remote probe nodes
ship.py         — probe node → central node shipper (watermark-based incremental delivery)
deprecations.py — model deprecation/migration calendar (only verified, sourced entries)
```

Each probe node runs with its own `REGION`, measures every provider, and writes to the time-series. For multi-region, remote nodes ship their measurements to a central node that aggregates and builds the site.

## Run it yourself (no keys needed)

```bash
git clone https://github.com/mazamaka/llm-latency-tracker
cd llm-latency-tracker
REGION=local python3 run.py         # take edge-latency measurements
python3 aggregate.py --region local # see the ranking from this location
```

Runs on plain Python 3.12+ (standard library). `httpx` / `loguru` are optional.

**Inference probes (real TTFT):**

```bash
cp .env.example .env                # add keys for the providers you want to measure
pip install -r requirements.txt
REGION=local python3 run.py
python3 aggregate.py --region local --type inference
```

**Build the site locally:**

```bash
BASE_URL=https://example.com python3 sitegen.py   # → ./site/
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q                              # tests
```

See [`deploy/`](../deploy/) for a container + a generic multi-region deployment guide.
