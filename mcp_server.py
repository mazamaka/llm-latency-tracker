#!/usr/bin/env python3
"""Standalone MCP server (stdio) for LLM Latency Tracker.

Read-only tools over the public llmlatency.dev JSON API (data CC BY 4.0):

* ``get_ai_api_latency``       — provider leaderboard for one region (or all regions)
* ``get_provider_latency``     — one provider across every probe region
* ``get_model_deprecations``   — model shutdown calendar and provider notice periods
* ``get_coding_agent_health``  — Claude Code / Codex speed, thinking and accuracy drift

Stdlib only, no keys, no local state. The hosted Streamable-HTTP endpoint
(https://llmlatency.dev/mcp) remains the zero-setup way to use the data; this
file lets the server run locally or be built from the repository.

    python3 mcp_server.py            # speaks MCP (JSON-RPC 2.0) over stdin/stdout
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

VERSION = "1.2.0"
BASE_URL = "https://llmlatency.dev"
SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
REGIONS = ("eu-hetzner", "us-central", "ap-tokyo", "sa-east")
AGENTS = ("claude-code", "codex")
CACHE_TTL_S = 300

INSTRUCTIONS = (
    "Independent measurements of hosted AI APIs from llmlatency.dev (CC BY 4.0; cite llmlatency.dev). "
    "Latency tools report network time to first byte (DNS+TCP+TLS+HTTP TTFB), not model generation speed. "
    "Use get_ai_api_latency to rank providers inside a region, get_provider_latency to compare one provider "
    "across regions, get_model_deprecations for model shutdown dates, and get_coding_agent_health for "
    "Claude Code / Codex slowdowns or accuracy drops. All tools are read-only and make one HTTPS GET."
)

_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

_NETWORK_ROW = {
    "type": "object",
    "properties": {
        "rank": {"type": "integer", "description": "1 = lowest median TTFB in the region"},
        "provider": {"type": "string", "description": "Provider id, e.g. openai, anthropic, groq"},
        "p50_ms": {"type": "number", "description": "Median time to first byte, milliseconds"},
        "p95_ms": {"type": "number", "description": "95th-percentile time to first byte, milliseconds"},
        "uptime_pct": {"type": "number", "description": "Share of probes that got an HTTP response, percent"},
        "samples": {"type": "integer", "description": "Probes in the window (288 = one every 5 minutes for 24 h)"},
    },
}

TOOLS = [
    {
        "name": "get_ai_api_latency",
        "title": "AI API latency leaderboard by region",
        "description": (
            "Rank hosted AI inference API providers (46 tracked: OpenAI, Anthropic, Google, Groq, Fireworks, "
            "OpenRouter and others) by measured network latency inside a probe region over the last 24 hours. "
            "Use it to answer 'which provider responds fastest from Europe/US/Tokyo/Sao Paulo' or to pick an "
            "endpoint close to your users. To follow one named provider across regions use get_provider_latency "
            "instead. Values are edge TTFB (DNS+TCP+TLS+first byte from probes every 5 minutes, direct "
            "connections, no gateway), not tokens per second or model quality. Returns the region label, "
            "dataset timestamp and the ranked rows; omit region to get all four regions at once (larger response)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "string",
                    "enum": list(REGIONS),
                    "description": (
                        "Probe location: eu-hetzner (Germany), us-central (USA), ap-tokyo (Japan), "
                        "sa-east (Sao Paulo). Omit to return every region."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Keep only the top N providers per region (default: all ranked providers).",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "generated": {"type": "string", "description": "UTC timestamp when the dataset was built"},
                "window": {"type": "string", "description": "Measurement window, e.g. last 24h"},
                "source": {"type": "string", "description": "Attribution URL (CC BY 4.0)"},
                "regions": {
                    "type": "object",
                    "description": "Region id -> {label, network: ranked rows}",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "description": "Human-readable region name"},
                            "network": {"type": "array", "items": _NETWORK_ROW},
                        },
                    },
                },
            },
            "required": ["generated", "regions"],
        },
        "annotations": {"title": "AI API latency leaderboard by region", **_READ_ONLY},
    },
    {
        "name": "get_provider_latency",
        "title": "One AI provider's latency in every region",
        "description": (
            "Show how a single AI API provider performs from every probe region (Germany, USA, Tokyo, "
            "Sao Paulo): its rank in that region, median and p95 time to first byte, uptime and sample count "
            "over the last 24 hours. Use it for questions like 'is the Anthropic API slower from Asia than from "
            "Europe' or 'was OpenAI reachable today'. For the full leaderboard of a region use "
            "get_ai_api_latency. An unknown provider id returns an error that lists the valid ids."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": (
                        "Provider id as published on llmlatency.dev, lowercase with hyphens, e.g. openai, "
                        "anthropic, google, groq, mistral, deepseek, openrouter, together, fireworks."
                    ),
                },
            },
            "required": ["provider"],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "Normalized provider id"},
                "generated": {"type": "string", "description": "UTC timestamp when the dataset was built"},
                "source": {"type": "string", "description": "Attribution URL (CC BY 4.0)"},
                "regions": {
                    "type": "array",
                    "description": "One row per region where the provider was measured",
                    "items": {
                        "type": "object",
                        "properties": {
                            "region": {"type": "string", "description": "Region id"},
                            "label": {"type": "string", "description": "Human-readable region name"},
                            "rank": {"type": "integer", "description": "Position among providers in that region"},
                            "providers_ranked": {"type": "integer", "description": "Providers ranked in that region"},
                            "p50_ms": {"type": "number", "description": "Median TTFB, milliseconds"},
                            "p95_ms": {"type": "number", "description": "95th-percentile TTFB, milliseconds"},
                            "uptime_pct": {"type": "number", "description": "Successful probes, percent"},
                            "samples": {"type": "integer", "description": "Probes in the 24 h window"},
                        },
                    },
                },
            },
            "required": ["provider", "regions"],
        },
        "annotations": {"title": "One AI provider's latency in every region", **_READ_ONLY},
    },
    {
        "name": "get_model_deprecations",
        "title": "AI model deprecation and shutdown calendar",
        "description": (
            "List announced shutdowns of AI models (OpenAI, Anthropic, Mistral, Azure OpenAI and others) with "
            "announcement date, shutdown date, recommended replacement and the provider page each entry was "
            "verified against, plus how many days of notice each provider typically gives (median/min/max). "
            "Use it before pinning a model version, to plan migrations, or to check whether a model ID still "
            "works. Returns upcoming shutdowns by default; set include_past to also get already-retired models. "
            "This is calendar data, not latency; for speed use get_ai_api_latency."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": (
                        "Filter to one provider, case-insensitive: openai, anthropic, mistral, azure-openai, "
                        "cohere. Omit for all providers."
                    ),
                },
                "include_past": {
                    "type": "boolean",
                    "description": "Also return models whose shutdown date has passed (default false).",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "generated": {"type": "string", "description": "UTC timestamp when the calendar was built"},
                "source": {"type": "string", "description": "Human-readable calendar page"},
                "notice_period_days": {
                    "type": "array",
                    "description": "Per provider: models counted and median/min/max days between announcement and shutdown",
                    "items": {"type": "object"},
                },
                "upcoming": {
                    "type": "array",
                    "description": "Models not yet shut down, soonest first",
                    "items": {
                        "type": "object",
                        "properties": {
                            "provider": {"type": "string", "description": "Provider name"},
                            "model": {"type": "string", "description": "Exact model ID"},
                            "announced": {"type": ["string", "null"], "description": "Announcement date, YYYY-MM-DD"},
                            "shutdown": {"type": ["string", "null"], "description": "Shutdown date, YYYY-MM-DD"},
                            "replacement": {"type": ["string", "null"], "description": "Suggested replacement"},
                            "source_url": {"type": "string", "description": "Provider page the entry was checked against"},
                            "notice_days": {"type": ["integer", "null"], "description": "Days from announcement to shutdown"},
                        },
                    },
                },
                "past": {"type": "array", "description": "Retired models (only with include_past)", "items": {"type": "object"}},
            },
            "required": ["upcoming"],
        },
        "annotations": {"title": "AI model deprecation and shutdown calendar", **_READ_ONLY},
    },
    {
        "name": "get_coding_agent_health",
        "title": "Claude Code / Codex speed and accuracy drift",
        "description": (
            "Report whether the Claude Code or Codex CLI coding agents (on consumer subscriptions) got slower, "
            "think less or answer worse than their own recent baseline. Gives output speed (tokens/s and ms per "
            "token), thinking tokens, time to first token and error rate for the last 24 h versus the previous "
            "7 days with Mann-Whitney significance flags, plus accuracy on a fixed hard question panel. Use it "
            "when someone asks 'is Claude Code slow / nerfed today'. Measured hourly from one EU server, one "
            "account per vendor, so it reflects that vantage point; for raw API latency use get_ai_api_latency."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "enum": list(AGENTS),
                    "description": "claude-code (Anthropic Claude Code) or codex (OpenAI Codex CLI); omit for both.",
                },
            },
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "properties": {
                "generated": {"type": "string", "description": "UTC timestamp of the export"},
                "vantage": {"type": "string", "description": "Where and how the agents are measured"},
                "rules": {"type": "object", "description": "Significance rules used for every verdict"},
                "agents": {
                    "type": "array",
                    "description": (
                        "Per agent: model, harness version, speed/thinking/ttft {recent, baseline, change, p, "
                        "significant}, errors_24h and hard-panel accuracy"
                    ),
                    "items": {"type": "object"},
                },
            },
            "required": ["agents"],
        },
        "annotations": {"title": "Claude Code / Codex speed and accuracy drift", **_READ_ONLY},
    },
]

_cache: dict[str, tuple[float, dict]] = {}


class ToolError(Exception):
    """A problem the caller can fix (bad argument) or should know about (upstream failure)."""


def fetch_json(path: str) -> dict:
    hit = _cache.get(path)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    req = urllib.request.Request(BASE_URL + path, headers={"User-Agent": f"llm-latency-tracker-mcp/{VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as e:  # surfaced to the agent as a tool error
        raise ToolError(f"llmlatency.dev API unavailable ({path}): {e}") from e
    _cache[path] = (time.monotonic(), data)
    return data


def _norm(s: object) -> str:
    return str(s or "").strip().lower().replace(" ", "-").replace("_", "-")


def tool_region_latency(args: dict) -> dict:
    region, limit = args.get("region"), args.get("limit")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise ToolError("limit must be a positive integer")
    data = fetch_json("/api/rankings.json")
    regions = data.get("regions") or {}
    if region is not None:
        if region not in regions:
            raise ToolError(f"unknown region {region!r}; valid: {', '.join(sorted(regions))}")
        regions = {region: regions[region]}
    if limit is not None:
        regions = {k: {**v, "network": (v.get("network") or [])[:limit]} for k, v in regions.items()}
    return {"generated": data.get("generated", ""), "window": "last 24h",
            "source": data.get("source", BASE_URL), "license": data.get("license", "CC-BY-4.0"),
            "unit": data.get("unit", "milliseconds"), "regions": regions}


def tool_provider_latency(args: dict) -> dict:
    want = _norm(args.get("provider"))
    if not want:
        raise ToolError("provider is required, e.g. openai or anthropic")
    data = fetch_json("/api/rankings.json")
    rows, known = [], set()
    for rid, reg in sorted((data.get("regions") or {}).items()):
        net = reg.get("network") or []
        for row in net:
            known.add(row.get("provider"))
            if _norm(row.get("provider")) == want:
                rows.append({"region": rid, "label": reg.get("label", rid), "providers_ranked": len(net),
                             **{k: row.get(k) for k in ("rank", "p50_ms", "p95_ms", "uptime_pct", "samples")}})
    if not rows:
        raise ToolError(f"provider {want!r} is not tracked; valid ids: {', '.join(sorted(p for p in known if p))}")
    return {"provider": want, "generated": data.get("generated", ""), "window": "last 24h",
            "source": data.get("source", BASE_URL), "license": data.get("license", "CC-BY-4.0"), "regions": rows}


def tool_deprecations(args: dict) -> dict:
    data = fetch_json("/api/deprecations.json")
    want = _norm(args.get("provider"))
    include_past = bool(args.get("include_past"))

    def pick(items: list | None) -> list:
        return [x for x in items or [] if not want or _norm(x.get("provider")) == want]

    out = {"generated": data.get("generated", ""), "source": data.get("source", BASE_URL + "/deprecations"),
           "license": data.get("license", "CC-BY-4.0"), "method": data.get("method", ""),
           "notice_period_days": pick(data.get("notice_period_days")), "upcoming": pick(data.get("upcoming"))}
    if include_past:
        out["past"] = pick(data.get("past"))
    if want and not (out["notice_period_days"] or out["upcoming"] or out.get("past")):
        names = {x.get("provider") for x in (data.get("upcoming") or []) + (data.get("past") or [])
                 + (data.get("notice_period_days") or [])}
        raise ToolError(f"no deprecation data for {want!r}; providers with data: "
                        f"{', '.join(sorted(_norm(n) for n in names if n))}")
    return out


def tool_coding_agents(args: dict) -> dict:
    agent = args.get("agent")
    if agent is not None and agent not in AGENTS:
        raise ToolError(f"unknown agent {agent!r}; valid: {', '.join(AGENTS)}")
    data = fetch_json("/api/coding-agents.json")
    agents = []
    for a in data.get("agents") or []:
        if agent and a.get("id") != agent:
            continue
        slim = {k: v for k, v in a.items() if k not in ("hourly", "daily")}  # raw series are large
        if isinstance(slim.get("hard"), dict):
            slim["hard"] = {k: v for k, v in slim["hard"].items() if k != "by_day"}
        agents.append(slim)
    return {"generated": data.get("generated", ""), "source": BASE_URL + "/coding-agents",
            "vantage": data.get("vantage", ""), "schedule": data.get("schedule", {}),
            "rules": data.get("rules", {}), "hard_panel": data.get("hard_panel", {}), "agents": agents}


HANDLERS = {
    "get_ai_api_latency": tool_region_latency,
    "get_provider_latency": tool_provider_latency,
    "get_model_deprecations": tool_deprecations,
    "get_coding_agent_health": tool_coding_agents,
}


def _ok(mid: object, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        proto = asked if asked in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return _ok(mid, {
            "protocolVersion": proto,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "llm-latency-tracker", "title": "LLM Latency Tracker", "version": VERSION,
                           "websiteUrl": BASE_URL},
            "instructions": INSTRUCTIONS,
        })
    if method == "tools/list":
        return _ok(mid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        fn = HANDLERS.get(name) if isinstance(name, str) else None
        if fn is None:
            return _err(mid, -32602, f"unknown tool {name!r}; available: {', '.join(HANDLERS)}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _err(mid, -32602, "arguments must be an object")
        try:
            out = fn(args)
        except ToolError as e:
            return _ok(mid, {"content": [{"type": "text", "text": str(e)}], "isError": True})
        return _ok(mid, {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                         "structuredContent": out})
    if method == "ping":
        return _ok(mid, {})
    if mid is None:  # notifications (e.g. notifications/initialized) need no reply
        return None
    return _err(mid, -32601, f"unknown method {method!r}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
            sys.stdout.flush()
            continue
        if not isinstance(msg, dict):
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
