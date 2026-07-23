"""Probes: network (edge/network latency, no key) and inference (real TTFT, with key).

The network probe is stdlib-only and works everywhere with no dependencies. It's precisely the distributed
running of this probe from many regions that produces the proprietary latency dataset.
"""
from __future__ import annotations

import concurrent.futures as cf
import socket
import ssl
import time
from datetime import datetime, timezone

from _log import logger
from config import Provider, PROBE_TIMEOUT, REGION
from db import Measurement


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve(host: str, port: int, timeout: float) -> None:
    """DNS resolution with a timeout. getaddrinfo has no timeout of its own and can hang
    forever (a stuck worker → run_once() never finishes) — so run it in a thread with a bounded result.
    shutdown(wait=False) so we don't block on a getaddrinfo that's still hanging."""
    ex = cf.ThreadPoolExecutor(max_workers=1)
    try:
        ex.submit(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM).result(timeout=timeout)
    finally:
        ex.shutdown(wait=False)


def measure_network(provider: Provider, region: str = REGION, timeout: float = PROBE_TIMEOUT) -> Measurement:
    """Measure network latency to the API host: DNS → TCP → TLS → TTFB.

    No key required: the provider returns 401/200, but timings and reachability are valid.
    This is exactly "how fast [provider] is reachable from [region]".
    """
    host, port, path = provider.host, provider.port, provider.models_path
    t0 = time.perf_counter()
    try:
        # DNS (bounded by timeout; the OS caches the timing after the 1st call, see README — not published as a standalone figure)
        _resolve(host, port, timeout)
        dns_ms = (time.perf_counter() - t0) * 1000

        # TCP connect
        t_c = time.perf_counter()
        raw = socket.create_connection((host, port), timeout=timeout)
        connect_ms = (time.perf_counter() - t_c) * 1000

        # TLS handshake (on handshake failure, explicitly close raw)
        ctx = ssl.create_default_context()
        try:
            t_t = time.perf_counter()
            sock = ctx.wrap_socket(raw, server_hostname=host)
            tls_ms = (time.perf_counter() - t_t) * 1000
        except BaseException:
            raw.close()
            raise

        # with guarantees close() even on a sendall/recv failure (a realistic provider reset during TTFB)
        with sock:
            sock.settimeout(timeout)          # read timeout: recv must not hang
            t_b = time.perf_counter()
            req = (
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                f"User-Agent: ai-latency-tracker/0.1\r\nAccept: */*\r\nConnection: close\r\n\r\n"
            )
            sock.sendall(req.encode())
            first = sock.recv(256)            # 256 more reliably catches the status line under fragmentation
            ttfb_ms = (time.perf_counter() - t_b) * 1000
            total_ms = (time.perf_counter() - t0) * 1000
            http_status = _parse_status(first)

        logger.info("network {} from {}: ttfb={:.0f}ms http={}", provider.name, region, ttfb_ms, http_status)
        return Measurement(
            ts=_now_iso(), provider=provider.name, region=region, probe_type="network",
            status="ok", dns_ms=round(dns_ms, 2), connect_ms=round(connect_ms, 2),
            tls_ms=round(tls_ms, 2), ttfb_ms=round(ttfb_ms, 2), total_ms=round(total_ms, 2),
            http_status=http_status,
        )
    except (socket.timeout, TimeoutError, cf.TimeoutError):
        logger.warning("network {} from {}: timeout", provider.name, region)
        return Measurement(ts=_now_iso(), provider=provider.name, region=region,
                           probe_type="network", status="timeout", error="timeout")
    except OSError as e:
        logger.warning("network {} from {}: error {}", provider.name, region, e)
        return Measurement(ts=_now_iso(), provider=provider.name, region=region,
                           probe_type="network", status="error", error=str(e))


def _parse_status(first_bytes: bytes) -> int | None:
    """Extract the HTTP code from the first line of the response (b'HTTP/1.1 401 ...')."""
    try:
        parts = first_bytes.split(b" ", 2)
        return int(parts[1]) if len(parts) >= 2 else None
    except (ValueError, IndexError):
        return None


def measure_inference(provider: Provider, region: str = REGION, timeout: float = PROBE_TIMEOUT) -> Measurement | None:
    """Measure the real TTFT (time-to-first-token) via streaming.

    Requires an API key in env (provider.api_key_env) and httpx. Spends ~1 token.
    Returns None if the key/endpoint/httpx are unavailable (the probe is simply skipped).
    """
    import os

    if not provider.inference_url or not provider.api_key_env:
        return None
    key = os.environ.get(provider.api_key_env)
    if not key:
        logger.debug("inference {}: no key {}, skipping", provider.name, provider.api_key_env)
        return None
    try:
        import httpx  # type: ignore
    except ImportError:
        logger.debug("inference {}: httpx not installed, skipping", provider.name)
        return None

    headers, payload = _inference_request(provider, key)
    # Explicit per-phase timeouts: short connect, read = timeout (otherwise a single overall one multiplies the ceiling).
    http_timeout = httpx.Timeout(timeout, connect=min(5.0, timeout))
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=http_timeout) as client, client.stream(
            "POST", provider.inference_url, headers=headers, json=payload
        ) as resp:
            ttft_ms: float | None = None
            for _chunk in resp.iter_bytes():
                if _chunk.strip():
                    ttft_ms = (time.perf_counter() - t0) * 1000
                    break
            total_ms = (time.perf_counter() - t0) * 1000
            status = "ok" if resp.status_code < 400 else "error"
            logger.info("inference {} from {}: ttft={} http={}", provider.name, region,
                        f"{ttft_ms:.0f}ms" if ttft_ms else "n/a", resp.status_code)
            return Measurement(
                ts=_now_iso(), provider=provider.name, region=region, probe_type="inference",
                model=provider.model, status=status, ttft_ms=round(ttft_ms, 2) if ttft_ms else None,
                total_ms=round(total_ms, 2), http_status=resp.status_code,
                error=None if status == "ok" else f"http {resp.status_code}",
            )
    except Exception as e:  # noqa: BLE001 - a probe must not crash, an error = data
        logger.warning("inference {} from {}: {}", provider.name, region, e)
        return Measurement(ts=_now_iso(), provider=provider.name, region=region,
                           probe_type="inference", model=provider.model, status="error", error=str(e))


def _inference_request(provider: Provider, key: str) -> tuple[dict, dict]:
    """Build headers and payload for a minimal streaming request tailored to a specific provider."""
    msg = [{"role": "user", "content": "ping"}]
    if provider.name == "anthropic":
        return (
            {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            {"model": provider.model, "max_tokens": 1, "stream": True, "messages": msg},
        )
    # OpenAI-compatible (openai, mistral, deepseek, xai, openrouter)
    return (
        {"Authorization": f"Bearer {key}", "content-type": "application/json"},
        {"model": provider.model, "max_tokens": 1, "stream": True, "messages": msg},
    )
