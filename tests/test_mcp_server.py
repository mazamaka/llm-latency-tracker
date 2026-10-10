"""MCP stdio server: protocol surface and tool logic, offline (the API fetch is stubbed)."""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mcp_server as m

RANKINGS = {
    "generated": "2026-10-10T07:22:13Z", "source": "https://llmlatency.dev", "license": "CC-BY-4.0",
    "unit": "milliseconds",
    "regions": {
        "eu-hetzner": {"label": "Europe (Germany)", "network": [
            {"rank": 1, "provider": "fireworks", "p50_ms": 96, "p95_ms": 197, "uptime_pct": 99, "samples": 288},
            {"rank": 2, "provider": "openai", "p50_ms": 120, "p95_ms": 210, "uptime_pct": 100, "samples": 288},
        ]},
        "ap-tokyo": {"label": "Asia (Tokyo)", "network": [
            {"rank": 1, "provider": "openai", "p50_ms": 18, "p95_ms": 53, "uptime_pct": 100, "samples": 288},
        ]},
    },
}
DEPRECATIONS = {
    "generated": "2026-10-10T07:22:13Z", "source": "https://llmlatency.dev/deprecations", "license": "CC-BY-4.0",
    "notice_period_days": [{"provider": "OpenAI", "models": 130, "median": 184, "min": 3, "max": 724}],
    "upcoming": [{"provider": "Azure OpenAI", "model": "gpt-4o", "announced": None, "shutdown": "2026-11-01",
                  "replacement": None, "source_url": "https://example.test", "verified": True, "notice_days": None}],
    "past": [{"provider": "OpenAI", "model": "gpt-old", "shutdown": "2026-01-01"}],
}
AGENTS = {"generated": "2026-10-10T06:59:01+00:00", "vantage": "EU", "rules": {}, "agents": [
    {"id": "claude-code", "speed": {"recent": 8.9}, "hourly": [1, 2], "daily": [3], "hard": {"accuracy": 0.6, "by_day": [1]}},
    {"id": "codex", "speed": {"recent": 30.0}, "hourly": [], "daily": [], "hard": {"accuracy": 0.5}},
]}
FIXTURES = {"/api/rankings.json": RANKINGS, "/api/deprecations.json": DEPRECATIONS, "/api/coding-agents.json": AGENTS}


def _call(name, args=None, monkeypatch=None):
    monkeypatch.setattr(m, "fetch_json", lambda path: FIXTURES[path])
    return m.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})


def test_initialize_negotiates_protocol():
    r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert r["result"]["serverInfo"]["name"] == "llm-latency-tracker"
    assert r["result"]["instructions"]
    r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert r["result"]["protocolVersion"] == m.SUPPORTED_PROTOCOLS[0]


def test_tools_list_is_complete_and_described():
    tools = m.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == list(m.HANDLERS)
    for t in tools:
        assert t["description"] and t["title"] and t["annotations"]["readOnlyHint"] is True
        assert t["outputSchema"]["type"] == "object"
        for prop in t["inputSchema"]["properties"].values():
            assert prop.get("description")


def test_region_latency(monkeypatch):
    r = _call("get_ai_api_latency", {"region": "eu-hetzner", "limit": 1}, monkeypatch)["result"]
    sc = r["structuredContent"]
    assert list(sc["regions"]) == ["eu-hetzner"] and len(sc["regions"]["eu-hetzner"]["network"]) == 1
    assert json.loads(r["content"][0]["text"]) == sc
    bad = _call("get_ai_api_latency", {"region": "mars"}, monkeypatch)["result"]
    assert bad["isError"] and "valid" in bad["content"][0]["text"]


def test_provider_latency(monkeypatch):
    sc = _call("get_provider_latency", {"provider": "OpenAI"}, monkeypatch)["result"]["structuredContent"]
    assert sc["provider"] == "openai" and {x["region"] for x in sc["regions"]} == {"eu-hetzner", "ap-tokyo"}
    bad = _call("get_provider_latency", {"provider": "nope"}, monkeypatch)["result"]
    assert bad["isError"] and "fireworks" in bad["content"][0]["text"]


def test_deprecations(monkeypatch):
    sc = _call("get_model_deprecations", {"provider": "azure-openai"}, monkeypatch)["result"]["structuredContent"]
    assert [x["model"] for x in sc["upcoming"]] == ["gpt-4o"] and "past" not in sc
    sc = _call("get_model_deprecations", {"provider": "openai", "include_past": True}, monkeypatch)["result"]["structuredContent"]
    assert [x["model"] for x in sc["past"]] == ["gpt-old"]
    assert _call("get_model_deprecations", {"provider": "nobody"}, monkeypatch)["result"]["isError"]


def test_coding_agents_are_slimmed(monkeypatch):
    sc = _call("get_coding_agent_health", {"agent": "claude-code"}, monkeypatch)["result"]["structuredContent"]
    assert [a["id"] for a in sc["agents"]] == ["claude-code"]
    assert "hourly" not in sc["agents"][0] and "by_day" not in sc["agents"][0]["hard"]


def test_unknown_tool_and_method():
    assert m.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "x"}})["error"]["code"] == -32602
    assert m.handle({"jsonrpc": "2.0", "id": 4, "method": "nope"})["error"]["code"] == -32601
    assert m.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_stdio_roundtrip():
    lines = "\n".join(json.dumps(x) for x in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )) + "\n"
    out = subprocess.run([sys.executable, os.path.join(ROOT, "mcp_server.py")], input=lines,
                         capture_output=True, text=True, timeout=30, check=True).stdout.strip().splitlines()
    assert [json.loads(x)["id"] for x in out] == [1, 2]
