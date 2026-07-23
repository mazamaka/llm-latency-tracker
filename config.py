"""Registry of providers and regions for the probe agent.

Each probe VPS runs with its own REGION (env), pings every provider
and writes measurements into a shared time series. The product's moat is the
accumulated archive of these measurements from distributed infrastructure (impossible to clone over a weekend).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Auto-load .env for non-Docker runs (REGION=... python3 run.py). Optional —
# if python-dotenv isn't installed, variables are read from the environment as-is.
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except ImportError:
    pass


@dataclass(frozen=True)
class Provider:
    """A single AI inference provider.

    host/path — for the network probe (edge/network latency, key NOT required).
    inference_url/model/api_key_env — for the inference probe (real TTFT, key required).
    """

    name: str
    host: str
    models_path: str                      # public GET endpoint for the network probe
    inference_url: str | None = None      # streaming chat endpoint
    model: str | None = None              # default model for the TTFT measurement
    api_key_env: str | None = None        # name of the env variable holding the key
    port: int = 443


# Public hosts. The network probe measures DNS→TCP→TLS→TTFB to models_path;
# a provider returns 401 without a key, but network timings and reachability are valid.
PROVIDERS: list[Provider] = [
    Provider("openai", "api.openai.com", "/v1/models",
             "https://api.openai.com/v1/chat/completions", "gpt-4o-mini", "OPENAI_API_KEY"),
    Provider("anthropic", "api.anthropic.com", "/v1/models",
             "https://api.anthropic.com/v1/messages", "claude-haiku-4-5-20251001", "ANTHROPIC_API_KEY"),
    Provider("google", "generativelanguage.googleapis.com", "/v1beta/models",
             None, "gemini-2.0-flash", "GEMINI_API_KEY"),
    Provider("mistral", "api.mistral.ai", "/v1/models",
             "https://api.mistral.ai/v1/chat/completions", "mistral-small-latest", "MISTRAL_API_KEY"),
    Provider("deepseek", "api.deepseek.com", "/v1/models",
             "https://api.deepseek.com/v1/chat/completions", "deepseek-chat", "DEEPSEEK_API_KEY"),
    Provider("xai", "api.x.ai", "/v1/models",
             "https://api.x.ai/v1/chat/completions", "grok-2-latest", "XAI_API_KEY"),
    Provider("openrouter", "openrouter.ai", "/api/v1/models",
             "https://openrouter.ai/api/v1/chat/completions", "openai/gpt-4o-mini", "OPENROUTER_API_KEY"),
    # OpenAI-compatible inference providers (fast inference — the key ones for latency comparison)
    Provider("groq", "api.groq.com", "/openai/v1/models",
             "https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    Provider("together", "api.together.xyz", "/v1/models",
             "https://api.together.xyz/v1/chat/completions", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "TOGETHER_API_KEY"),
    Provider("fireworks", "api.fireworks.ai", "/inference/v1/models",
             "https://api.fireworks.ai/inference/v1/chat/completions", "accounts/fireworks/models/llama-v3p3-70b-instruct", "FIREWORKS_API_KEY"),
    Provider("cerebras", "api.cerebras.ai", "/v1/models",
             "https://api.cerebras.ai/v1/chat/completions", "llama-3.3-70b", "CEREBRAS_API_KEY"),
    # Chinese providers (international endpoints, OpenAI-compatible where possible)
    Provider("glm", "api.z.ai", "/api/paas/v4/models",
             "https://api.z.ai/api/paas/v4/chat/completions", "glm-4.6", "ZHIPU_API_KEY"),
    Provider("kimi", "api.moonshot.ai", "/v1/models",
             "https://api.moonshot.ai/v1/chat/completions", "kimi-k2-0711-preview", "MOONSHOT_API_KEY"),
    Provider("qwen", "dashscope-intl.aliyuncs.com", "/compatible-mode/v1/models",
             "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions", "qwen-plus", "DASHSCOPE_API_KEY"),
    # Not OpenAI-compatible — network probe only (inference_url=None, to avoid sending a broken request)
    Provider("cohere", "api.cohere.com", "/v1/models"),
    Provider("minimax", "api.minimaxi.com", "/v1/text/chatcompletion_v2"),

    # Global coverage — network probe (edge latency across regions, no keys).
    # All hosts checked for reachability on 23.07.2026. inference_url=None (network only).
    Provider("perplexity", "api.perplexity.ai", "/chat/completions"),
    Provider("meta-llama", "api.llama.com", "/v1/models"),
    Provider("ai21", "api.ai21.com", "/studio/v1/chat/completions"),
    Provider("reka", "api.reka.ai", "/v1/models"),
    Provider("deepinfra", "api.deepinfra.com", "/v1/openai/models"),
    Provider("replicate", "api.replicate.com", "/v1/models"),
    Provider("novita", "api.novita.ai", "/v3/openai/models"),
    Provider("hyperbolic", "api.hyperbolic.xyz", "/v1/models"),
    Provider("sambanova", "api.sambanova.ai", "/v1/models"),
    Provider("nebius", "api.studio.nebius.com", "/v1/models"),
    Provider("featherless", "api.featherless.ai", "/v1/models"),
    Provider("inference-net", "api.inference.net", "/v1/models"),
    Provider("siliconflow", "api.siliconflow.com", "/v1/models"),
    Provider("aleph-alpha", "api.aleph-alpha.com", "/version"),
    Provider("writer", "api.writer.com", "/v1/models"),
    Provider("upstage", "api.upstage.ai", "/v1/solar/chat/completions"),
    Provider("friendli", "api.friendli.ai", "/v1/models"),
    Provider("nscale", "inference.api.nscale.com", "/v1/models"),
    Provider("baseten", "inference.baseten.co", "/v1/models"),
    Provider("targon", "api.targon.com", "/v1/models"),
    Provider("sarvam", "api.sarvam.ai", "/v1/models"),
    # Chinese (additional)
    Provider("baichuan", "api.baichuan-ai.com", "/v1/models"),
    Provider("yi-01ai", "api.lingyiwanwu.com", "/v1/models"),
    Provider("stepfun", "api.stepfun.com", "/v1/models"),
    Provider("hunyuan", "api.hunyuan.cloud.tencent.com", "/v1/models"),
    Provider("doubao", "ark.cn-beijing.volces.com", "/api/v3/models"),
    Provider("iflytek", "spark-api-open.xf-yun.com", "/v1/models"),
    Provider("ernie", "qianfan.baidubce.com", "/v2/models"),
    Provider("sensenova", "api.sensenova.cn", "/v1/models"),
]

# Region this probe runs from (each VPS sets its own). local — for debugging.
REGION: str = os.environ.get("REGION", "local")

# Timeout for a single network operation, seconds.
PROBE_TIMEOUT: float = float(os.environ.get("PROBE_TIMEOUT", "10"))

# Path to the SQLite time-series file.
DB_PATH: str = os.environ.get("DB_PATH", "measurements.db")

# Retention: raw samples older than N days are deleted (impacts the central node's disk).
# ~1.5 MB/day/region → 90 days × 5 regions ≈ <1 GB. Keeps the footprint bounded.
RETENTION_DAYS: int = int(os.environ.get("RETENTION_DAYS", "90"))


def providers_by_name(names: list[str] | None = None) -> list[Provider]:
    """Filter the registry by names (None → all)."""
    if not names:
        return PROVIDERS
    wanted = {n.lower() for n in names}
    return [p for p in PROVIDERS if p.name in wanted]
