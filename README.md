# LLM Latency Tracker

**Independent, provider-neutral latency & uptime for AI inference APIs — measured, not scraped.**

🌐 **Live: [llmlatency.dev](https://llmlatency.dev)** · 📊 [JSON API](https://llmlatency.dev/api/rankings.json) · 🤖 [MCP server](https://llmlatency.dev/mcp) · 🗓️ [Deprecation calendar](https://llmlatency.dev/deprecations)

![License](https://img.shields.io/badge/code-MIT-blue) ![Data](https://img.shields.io/badge/data-CC--BY--4.0-green) ![Agent-Ready](https://img.shields.io/badge/agent--ready-Level%205-orange) ![Python](https://img.shields.io/badge/python-3.12%2B-3776ab)

Most "AI API latency" numbers come from the providers themselves, or from a benchmark run once and never updated. This project **measures** it continuously, from multiple regions, and publishes the result as an open dataset.

- **Edge latency** — full DNS → TCP → TLS → time-to-first-byte, measured with the Python standard library (no API key required).
- **Inference latency** — real time-to-first-token via a streaming request (optional, needs a provider key).
- **Uptime** — success rate per provider, per region.
- **Regions** — Europe (Germany), US (Central), Asia (Tokyo), South America (São Paulo). More welcome.
- **~45 providers** — OpenAI, Anthropic, Google, Mistral, DeepSeek, xAI, Groq, Together, Fireworks, Cerebras, OpenRouter, Perplexity, plus Chinese models (GLM/Zhipu, Kimi/Moonshot, Qwen, MiniMax) and many more.
- **Deprecation calendar** — upcoming model retirements + migration targets, verified from official provider docs.

The site is a self-updating static site (Cloudflare Pages). The value isn't the code — it's the continuously-accumulated, distributed measurement archive. The code is open so the methodology is transparent.

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
ship.py         — probe node → central node shipper (watermark-based, never loses data on outage)
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
python3 -m pytest -q                              # tests
```

See [`deploy/`](deploy/) for a container + a generic multi-region deployment guide.

## Contributing

Especially welcome:

- **New providers** — add a `Provider(...)` entry in [`config.py`](config.py) (host + public models endpoint is enough for edge probes).
- **New regions** — spin up a probe node in a new location and ship to a central node.
- **Fixes & tests** — CI runs `pytest` + `ruff` on every push.

See **[CONTRIBUTING.md](CONTRIBUTING.md)** for dev setup, how to add a provider/region, and PR guidelines. Please keep the project's principle: **measured, not scraped, and honest about the dataset's age.**

## License

- **Code:** [MIT](LICENSE)
- **Data** (rankings, API output): **CC-BY-4.0** — attribute [llmlatency.dev](https://llmlatency.dev).

<!-- DATASET:BEGIN -->

### Daily snapshot — 2026-08-19

Measured latency across **45 AI inference providers** in 4 regions. Method: distributed edge (DNS→TCP→TLS→TTFB) + inference (TTFT) probes, last 24h. License: CC-BY-4.0.

| Region | Fastest provider (p50) | p50 | p95 | Uptime |
|---|---|---|---|---|
| Asia (Tokyo) | fireworks | 17 ms | 62 ms | 100% |
| Europe (Germany) | nscale | 97 ms | 198 ms | 100% |
| South America (São Paulo) | openrouter | 58 ms | 105 ms | 100% |
| US (Central) | google | 45 ms | 132 ms | 100% |

- Full dataset: [`data/rankings/2026-08-19.json`](data/rankings/2026-08-19.json) ([latest](data/rankings/latest.json))
- Citable archive (DOI): [`10.5281/zenodo.21954788`](https://doi.org/10.5281/zenodo.21954788) — daily aggregates, CC-BY-4.0
- Hugging Face dataset: <https://huggingface.co/datasets/llmlatency/llm-latency-tracker>
- Kaggle dataset: <https://www.kaggle.com/datasets/llmlatency/llm-latency-tracker>
- Archived in Software Heritage: [`swh:1:snp:2778cbabd72a70a629ee35fbd5ac536d1ccb7a9a`](https://archive.softwareheritage.org/swh:1:snp:2778cbabd72a70a629ee35fbd5ac536d1ccb7a9a)
- Python client: <https://pypi.org/project/llmlatency/>
- Live rankings and methodology: <https://llmlatency.dev>
- Machine-readable API: <https://llmlatency.dev/api/rankings.json>
- Model deprecation calendar: <https://llmlatency.dev/deprecations>

_Snapshot generated 2026-08-19T07:47:19Z — this table is regenerated daily._

<!-- DATASET:END -->
