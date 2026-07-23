# Contributing

Thanks for your interest! This project measures latency and uptime for AI inference APIs and publishes it as an open dataset. Contributions that make the measurements broader or more accurate are especially welcome.

## Ways to help

- **Add a provider** — the highest-value contribution. See below.
- **Add a region** — run a probe node somewhere new and ship to a central node.
- **Fix bugs / improve the prober or site generator.**
- **Improve accuracy** — better probe methodology, edge cases, error handling.

Please keep the project's core principle: **measured, not scraped, and honest about the dataset's age.** No fabricated numbers.

## Dev setup

Runs on plain Python 3.12+ (standard library). `httpx` / `loguru` are optional.

```bash
git clone https://github.com/mazamaka/llm-latency-tracker
cd llm-latency-tracker
python3 -m pip install -r requirements.txt   # optional extras + dev tools
REGION=local python3 run.py                   # take edge-latency measurements
python3 aggregate.py --region local           # see the ranking from this location
```

Build the site locally:

```bash
BASE_URL=https://example.com python3 sitegen.py   # → ./site/
```

## Before you open a PR

```bash
python3 -m pytest -q     # tests must pass
ruff check .             # lint must be clean
```

CI runs both on every push.

## Adding a provider

Add one `Provider(...)` entry to [`config.py`](config.py). For edge (network) probes you only need the host and a public GET endpoint — **no API key**:

```python
Provider("my-provider", "api.my-provider.com", "/v1/models"),
```

For inference (TTFT) probes, also set the streaming chat endpoint, a default model, and the env var name for the key:

```python
Provider("my-provider", "api.my-provider.com", "/v1/models",
         "https://api.my-provider.com/v1/chat/completions", "some-model", "MY_PROVIDER_API_KEY"),
```

Guidelines:
- The provider `name` must match `^[a-z0-9][a-z0-9_-]{0,63}$` (it's validated and used in URLs/DB).
- Prefer the provider's **official, public** models/list endpoint for the edge probe.
- Verify the host actually resolves and responds before submitting.
- If it isn't OpenAI-compatible, leave `inference_url=None` (edge probe only) rather than sending a malformed request.

## PR guidelines

- Keep PRs focused — one logical change each.
- Match the surrounding code style; keep it dependency-light (the core prober is stdlib-only on purpose).
- Explain **what** and **why** in the PR description.
- Be honest about limitations; don't overstate what a measurement proves.

## License

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE) (code); published measurement data is CC-BY-4.0.
