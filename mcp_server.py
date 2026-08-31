#!/usr/bin/env python3
"""Standalone MCP server (stdio) for LLM Latency Tracker.

Exposes one tool, `get_ai_api_latency`, backed by the public JSON API at
https://llmlatency.dev/api/rankings.json — the same measured data the site
publishes (CC BY 4.0). Stdlib only, no keys, no state: the container's job is
to start, answer introspection, and proxy one read-only call.

The hosted Streamable-HTTP endpoint (https://llmlatency.dev/mcp) remains the
primary way to use this server; this file exists so the server can also be
run locally or built from the repository (e.g. by directory checks that
require a Dockerfile).

    python3 mcp_server.py            # speaks MCP over stdin/stdout
"""
from __future__ import annotations

import json
import sys
import urllib.request

API_URL = "https://llmlatency.dev/api/rankings.json"
PROTOCOL = "2025-06-18"

TOOL = {
    "name": "get_ai_api_latency",
    "description": (
        "Measured latency (TTFB p50/p95) and uptime rankings of AI inference API "
        "providers by region. Independent probes every 5 minutes from 4 regions "
        "(eu-hetzner, us-central, ap-tokyo, sa-east); direct connections, no gateway. "
        "Data CC BY 4.0 from llmlatency.dev."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "region": {
                "type": "string",
                "description": "eu-hetzner, us-central, ap-tokyo or sa-east; omit for all regions",
            }
        },
    },
}


def fetch_rankings(region: str | None) -> dict:
    req = urllib.request.Request(API_URL, headers={"User-Agent": "llm-latency-tracker-mcp/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    if region and isinstance(data.get("regions"), dict) and region in data["regions"]:
        return data["regions"][region]
    return data.get("regions", data)


def handle(msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "llm-latency-tracker", "version": "1.1.0"},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [TOOL]}}
    if method == "tools/call":
        params = msg.get("params") or {}
        if params.get("name") != TOOL["name"]:
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": f"unknown tool {params.get('name')!r}"}}
        region = (params.get("arguments") or {}).get("region")
        try:
            out = fetch_rankings(region)
        except Exception as e:  # network errors become tool errors, not crashes
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": f"upstream error: {e}"}], "isError": True}}
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": json.dumps(out)}]}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if mid is None:  # notifications (e.g. notifications/initialized) need no reply
        return None
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method!r}"}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
