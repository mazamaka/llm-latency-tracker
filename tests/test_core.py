"""Pure-logic tests. Run: python3 -m pytest -q  (or python3 tests/test_core.py without pytest)."""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aggregate import _percentile, ttfb_ranking
from probe import _parse_status
from db import init_db, insert, connect, Measurement, InvalidIdentifier
from deprecations import load, upcoming, Deprecation


def test_percentile_basic():
    assert _percentile([], 50) == 0.0
    assert _percentile([10], 50) == 10
    assert _percentile([10, 20, 30, 40], 50) in (20, 30)
    assert _percentile([1, 2, 3, 4, 5], 95) == 5


def test_parse_status():
    assert _parse_status(b"HTTP/1.1 401 Unauthorized") == 401
    assert _parse_status(b"HTTP/1.1 200 OK") == 200
    assert _parse_status(b"garbage") is None
    assert _parse_status(b"") is None


def test_db_roundtrip_and_ranking():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "t.db")
        init_db(db)
        for prov, ttfb in [("openai", 200), ("google", 50), ("google", 70)]:
            insert(db, Measurement(ts="2026-07-23T10:00:00+00:00", provider=prov, region="local",
                                   probe_type="network", status="ok", ttfb_ms=ttfb))
        # the freshness filter in ttfb_ranking looks at datetime('now'); use a large window
        ranking = ttfb_ranking(db, "local", hours=24 * 3650, probe_type="network")
        by = {r.provider: r for r in ranking}
        assert by["google"].samples == 2
        assert by["google"].p50_ms in (50, 70)
        assert by["openai"].ok_rate == 100.0
        # google is faster than openai → higher in the ranking
        assert ranking[0].provider == "google"


def test_deprecations_load_and_upcoming():
    entries = load()  # reads the real data/deprecations.json
    assert isinstance(entries, list)
    # seed entries are not verified → still rendered, but flagged
    for e in entries:
        assert isinstance(e, Deprecation)
        assert e.source_url  # source is required
    fake = [
        Deprecation("x", "m1", None, "2020-01-01", "m2", "http://s", True),   # past
        Deprecation("x", "m2", None, "2999-01-01", "m3", "http://s", True),   # future
        Deprecation("x", "m3", None, None, "m4", "http://s", False),          # open-ended, unverified
    ]
    up = upcoming(fake)
    models = [d.model for d in up]
    assert "m1" not in models          # past filtered out
    assert "m2" in models and "m3" in models
    assert up[0].model == "m2"         # verified with a date — first


def test_sitegen_builds():
    import sitegen
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "t.db")
        init_db(db)
        insert(db, Measurement(ts="2026-07-23T10:00:00+00:00", provider="google", region="local",
                               probe_type="network", status="ok", ttfb_ms=55))
        out = os.path.join(d, "site")
        n = sitegen.build(db, out)
        assert n >= 3
        assert os.path.exists(os.path.join(out, "index.html"))
        assert os.path.exists(os.path.join(out, "llms.txt"))
        assert os.path.exists(os.path.join(out, "deprecations.html"))
        assert os.path.exists(os.path.join(out, "region", "local.html"))
        html = open(os.path.join(out, "region", "local.html")).read()
        assert "fastest AI inference API" in html
        assert "application/ld+json" in html      # schema.org present


def test_insert_rejects_bad_identifiers():
    """CRITICAL regression: path-traversal / injection into region/provider are rejected on write."""
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "t.db")
        init_db(db)
        bad = ["../../evil", "eu-west/prod", 'a"><svg onload=alert(1)>', "a/b", "UPPER", "a b", ""]
        for value in bad:
            for field in ("region", "provider"):
                m = Measurement(ts="2026-07-23T10:00:00+00:00",
                                provider="openai" if field == "region" else value,
                                region=value if field == "region" else "local",
                                probe_type="network", status="ok", ttfb_ms=10)
                raised = False
                try:
                    insert(db, m)
                except InvalidIdentifier:
                    raised = True
                assert raised, f"bad {field}={value!r} must be rejected"
        # clean values pass
        insert(db, Measurement(ts="2026-07-23T10:00:00+00:00", provider="openai",
                               region="eu-west", probe_type="network", status="ok", ttfb_ms=10))


def test_jsonld_script_escapes():
    """</script> inside JSON-LD must not close the tag prematurely."""
    import sitegen
    out = sitegen._jsonld_script({"x": "</script><svg onload=alert(1)>"})
    assert out.endswith("</script>")                          # our closing tag
    assert "</script>" not in out[:-len("</script>")]         # from data — escaped
    assert "\\u003c" in out and "\\u003e" in out


def test_percentile_deterministic():
    """Nearest-rank round-half-up: deterministic and stable."""
    assert _percentile([10, 20], 50) == 20                    # idx=floor(0.5+0.5)=1
    assert _percentile([1, 2, 3], 100) == 3
    assert _percentile([1, 2, 3], 0) == 1
    assert _percentile([1, 2, 3, 4, 5], 50) == 3
    assert _percentile([10, 20], 50) == _percentile([10, 20], 50)  # repeatability


def _start_ingest(central_db, token):
    """Start an ingest server on a free localhost port in the background. Return (httpd, url)."""
    import socket as _s
    from http.server import ThreadingHTTPServer
    import threading
    import ingest as ing
    ing.INGEST_TOKEN = token
    ing._Handler.db_path = central_db
    from db import init_db
    init_db(central_db)
    sock = _s.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), ing._Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}/ingest"


def test_ingest_ship_end_to_end():
    """probe writes locally → ship_pending sends to ingest → center receives; watermark advances."""
    from ship import ship_pending, _read_watermark
    with tempfile.TemporaryDirectory() as d:
        local, central = os.path.join(d, "p.db"), os.path.join(d, "c.db")
        token = "test-secret-token-123"
        httpd, url = _start_ingest(central, token)
        try:
            init_db(local)
            for i in range(3):
                insert(local, Measurement(ts=f"2026-07-23T10:00:0{i}+00:00", provider="google",
                                          region="us-east", probe_type="network", status="ok", ttfb_ms=40 + i))
            shipped = ship_pending(local, url, token)
            assert shipped == 3, shipped
            with connect(central) as c:
                got = c.execute("SELECT count(*) FROM measurements WHERE region='us-east'").fetchone()[0]
            assert got == 3, got
            assert _read_watermark(local) == 3
            # a repeat ship doesn't duplicate (watermark)
            assert ship_pending(local, url, token) == 0
        finally:
            httpd.shutdown()


def test_ingest_rejects_bad_token():
    """Wrong token → 401, nothing reaches the center."""
    from ship import ship_pending
    with tempfile.TemporaryDirectory() as d:
        local, central = os.path.join(d, "p.db"), os.path.join(d, "c.db")
        httpd, url = _start_ingest(central, "correct-token")
        try:
            init_db(local)
            insert(local, Measurement(ts="2026-07-23T10:00:00+00:00", provider="google",
                                      region="us-east", probe_type="network", status="ok", ttfb_ms=40))
            shipped = ship_pending(local, url, "WRONG-token")   # wrong token
            assert shipped == 0
            with connect(central) as c:
                got = c.execute("SELECT count(*) FROM measurements").fetchone()[0]
            assert got == 0, got
        finally:
            httpd.shutdown()


def _run_without_pytest() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_without_pytest() else 0)
