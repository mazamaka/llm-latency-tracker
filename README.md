# 📊 LLM Latency Tracker

**Measure AI API latency and uptime across regions, and publish the results as open data.**

🌐 **[Live rankings](https://llmlatency.dev)** · **[JSON API](https://llmlatency.dev/api/rankings.json)** · **[MCP endpoint](https://llmlatency.dev/mcp)**

## ⚡ What it does

- 🌍 **Regional measurements** — compare provider response times and availability from distributed probe nodes.
- ⏱️ **Two probe types** — network time to first byte (TTFB) and optional inference time to first token (TTFT).
- 📈 **Open dataset** — daily rankings, historical snapshots and regional p50 / p95 statistics.
- 🤖 **Agent access** — JSON API, MCP, Markdown pages and an `llms.txt` index.
- 🗓️ **Model lifecycle** — a deprecation calendar with links to provider announcements.

## 🔧 Built for measurement

- **Python standard-library core** for edge probes; provider keys are needed for inference probes.
- **SQLite storage** and regional aggregation, with remote nodes shipping measurements to a central node.
- **Static publishing** for the website and JSON datasets.
- **CI checks** for tests and linting; published snapshots include their measurement date.

**Reading the results:** edge TTFB measures network/API responsiveness, not model generation speed. Compare it separately from inference TTFT; results depend on region, probe type and time window.

## 🚀 Quick start

Use the public data without installing anything:

```bash
curl https://llmlatency.dev/api/rankings.json
```

Or run edge probes locally with **Python 3.12+**:

```bash
git clone https://github.com/mazamaka/llm-latency-tracker.git
cd llm-latency-tracker
REGION=local python3 run.py
python3 aggregate.py --region local
```

For a local stdio MCP server over the published API:

```bash
python3 mcp_server.py
```

**[Inference probes, API access & site generation →](docs/USAGE.md)** · **[Deployment →](deploy/)**

## 🧪 Contributing

Add a provider, bring a new probe region, or improve the measurement code. See **[CONTRIBUTING.md](CONTRIBUTING.md)** for setup and checks.

---

**Python · SQLite · JSON API · MCP · Cloudflare Pages**

**[Code: MIT](LICENSE)** · **Data: CC-BY-4.0**, with attribution to [llmlatency.dev](https://llmlatency.dev).

<details>
<summary><b>📈 Latest daily snapshot</b></summary>

<!-- DATASET:BEGIN -->

### Daily snapshot — 2026-10-07

Measured latency across **46 AI inference providers** in 4 regions. Method: distributed edge (DNS→TCP→TLS→TTFB) + inference (TTFT) probes, last 24h. License: CC-BY-4.0.

| Region | Fastest provider (p50) | p50 | p95 | Uptime |
|---|---|---|---|---|
| Asia (Tokyo) | fireworks | 18 ms | 68 ms | 100% |
| Europe (Germany) | fireworks | 98 ms | 200 ms | 100% |
| South America (São Paulo) | openrouter | 60 ms | 93 ms | 100% |
| US (Central) | fireworks | 29 ms | 79 ms | 100% |

- Full dataset: [`data/rankings/2026-10-07.json`](data/rankings/2026-10-07.json) ([latest](data/rankings/latest.json))
- Citable archive (DOI): [`10.5281/zenodo.22764636`](https://doi.org/10.5281/zenodo.22764636) — daily aggregates, CC-BY-4.0
- Hugging Face dataset: <https://huggingface.co/datasets/llmlatency/llm-latency-tracker>
- Kaggle dataset: <https://www.kaggle.com/datasets/llmlatency/llm-latency-tracker>
- Archived in Software Heritage: [`swh:1:snp:2778cbabd72a70a629ee35fbd5ac536d1ccb7a9a`](https://archive.softwareheritage.org/swh:1:snp:2778cbabd72a70a629ee35fbd5ac536d1ccb7a9a)
- Python client: <https://pypi.org/project/llmlatency/>
- Live rankings and methodology: <https://llmlatency.dev>
- Machine-readable API: <https://llmlatency.dev/api/rankings.json>
- Model deprecation calendar: <https://llmlatency.dev/deprecations>

_Snapshot generated 2026-10-07T07:21:29Z — this table is regenerated daily._

<!-- DATASET:END -->

</details>
